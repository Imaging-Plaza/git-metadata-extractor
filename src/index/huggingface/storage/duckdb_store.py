"""DuckDB lifecycle, schema bootstrap, and upsert helpers."""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb

from src.index.huggingface.paths import get_huggingface_paths

if TYPE_CHECKING:
    from collections.abc import Iterator

LOGGER = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

ENTITY_TABLES: tuple[str, ...] = ("models", "datasets", "spaces")


def _repo_iri_for_table(entity_table: str, repo_id: str) -> str:
    """Promote a bare `<author>/<name>` to its canonical IRI for the table."""
    from src.index.huggingface.iri import (  # noqa: PLC0415
        dataset_iri,
        model_iri,
        space_iri,
    )

    match entity_table:
        case "models":
            return model_iri(repo_id)
        case "datasets":
            return dataset_iri(repo_id)
        case "spaces":
            return space_iri(repo_id)
    return repo_id  # unknown table — pass through


def _load_schema_sql() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


class DuckDBStore:
    """Thin wrapper around DuckDB tuned for the HuggingFace schema.

    Construct with `DuckDBStore.open()` for the default repo path.
    `bootstrap()` is idempotent.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: duckdb.DuckDBPyConnection | None = None

    @classmethod
    def open(cls, db_path: Path | None = None) -> DuckDBStore:
        if db_path is None:
            db_path = get_huggingface_paths().duckdb_path
        store = cls(db_path)
        store.bootstrap()
        return store

    def connect(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = duckdb.connect(str(self.db_path))
        return self._conn

    def bootstrap(self) -> None:
        conn = self.connect()
        conn.execute(_load_schema_sql())
        # Promote bare slugs / repo_ids to their canonical
        # `https://huggingface.co/...` IRI form. Idempotent — rows
        # already in URL shape match no WHERE clause.
        self._migrate_to_iri_ids()
        # Backfill the citation-surface columns (`arxiv_dois` on
        # models, `citation_text` / `paperswithcode_url` /
        # `citation_dois` on datasets) from existing `raw` payloads.
        # Skipped when the columns already carry a non-null value.
        self._migrate_citation_surface()

    def _migrate_citation_surface(self) -> None:
        """Backfill arxiv DOIs (from model tags) and dataset citation
        metadata (from `raw.citation` / `raw.paperswithcode_id`).
        """
        from src.index.huggingface.iri import (  # noqa: PLC0415
            arxiv_dois_from_tags,
            dois_from_bibtex,
            paperswithcode_url,
        )

        conn = self.connect()

        # --- models.arxiv_dois -----------------------------------------
        # The arxiv tags live in `tags` JSON; backfill via Python because
        # the parsing (strip `arxiv:`, drop `v<n>` suffix, dedupe) is
        # cleaner here than a SQL CASE.
        rows = conn.execute(
            "SELECT repo_id, tags FROM models "
            "WHERE arxiv_dois IS NULL OR arxiv_dois = '[]' OR arxiv_dois = 'null'",
        ).fetchall()
        for repo_id, tags_payload in rows:
            try:
                tags = (
                    json.loads(tags_payload)
                    if isinstance(tags_payload, str)
                    else tags_payload
                )
            except json.JSONDecodeError:
                continue
            dois = arxiv_dois_from_tags(tags or [])
            if dois:
                conn.execute(
                    "UPDATE models SET arxiv_dois = ? WHERE repo_id = ?",
                    [json.dumps(dois, ensure_ascii=False), repo_id],
                )

        # --- datasets.citation_text / paperswithcode_url / citation_dois -
        rows = conn.execute(
            "SELECT repo_id, raw FROM datasets "
            "WHERE citation_text IS NULL "
            "   OR paperswithcode_url IS NULL "
            "   OR citation_dois IS NULL OR citation_dois = '[]' OR citation_dois = 'null'",
        ).fetchall()
        for repo_id, raw_payload in rows:
            try:
                raw = (
                    json.loads(raw_payload)
                    if isinstance(raw_payload, str)
                    else raw_payload
                )
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, dict):
                continue
            citation = raw.get("citation") if isinstance(raw.get("citation"), str) else None
            pwc_id = raw.get("paperswithcode_id") or raw.get("paperswithcodeId")
            citation_dois = dois_from_bibtex(citation)
            pwc_url = paperswithcode_url(pwc_id) if pwc_id else None
            if citation or pwc_url or citation_dois:
                conn.execute(
                    "UPDATE datasets SET "
                    "  citation_text = COALESCE(citation_text, ?), "
                    "  paperswithcode_url = COALESCE(paperswithcode_url, ?), "
                    "  citation_dois = COALESCE("
                    "      CASE WHEN citation_dois IS NULL "
                    "                OR citation_dois = '[]' "
                    "                OR citation_dois = 'null' "
                    "           THEN NULL ELSE citation_dois END, "
                    "      ?) "
                    "WHERE repo_id = ?",
                    [
                        citation,
                        pwc_url,
                        json.dumps(citation_dois, ensure_ascii=False)
                        if citation_dois else None,
                        repo_id,
                    ],
                )

    def _migrate_to_iri_ids(self) -> None:
        """CTAS-swap each table whose PK or FK still carries bare ids.

        DuckDB's `UPDATE … SET <indexed-col> = …` chokes on large indexed
        tables (we hit it on Zenodo's 24k record_creators), so the link
        and entity tables get a fresh table + INSERT + DROP + RENAME.
        Each step checks for at least one bare-id row before doing
        any work, so the migration is a no-op on already-migrated DBs.
        """
        conn = self.connect()
        ns = "https://huggingface.co/"

        def _table_has_bare(table: str, column: str) -> bool:
            row = conn.execute(
                f"SELECT 1 FROM {table} WHERE {column} IS NOT NULL "
                f"AND {column} NOT LIKE 'https://%' LIMIT 1",
            ).fetchone()
            return row is not None

        def _ctas_swap(
            *, table: str, columns: list[tuple[str, str]], select: str,
            primary_key: tuple[str, ...] | None = None,
        ) -> None:
            # The DROP-old / RENAME-new sequence is only atomic when
            # wrapped in a transaction. Without BEGIN/COMMIT, a crash
            # between the two statements leaves the database in a state
            # where the original table has been deleted but the
            # replacement is still under its temp name — the migration
            # has to be hand-recovered from the WAL. The transient
            # working table (CREATE + INSERT) doesn't need the
            # transaction, but a single span keeps the rollback
            # surface simple: any failure restores the pre-migration
            # state in full.
            new_table = f"{table}__iri_migrate"
            conn.execute(f"DROP TABLE IF EXISTS {new_table}")
            col_defs = ", ".join(f"{name} {dtype}" for name, dtype in columns)
            if primary_key:
                col_defs += f", PRIMARY KEY ({', '.join(primary_key)})"
            conn.execute("BEGIN TRANSACTION")
            try:
                conn.execute(f"CREATE TABLE {new_table} ({col_defs})")
                conn.execute(f"INSERT INTO {new_table} {select}")
                conn.execute(f"DROP TABLE {table}")
                conn.execute(f"ALTER TABLE {new_table} RENAME TO {table}")
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

        # --- orgs.slug (PK) → IRI -------------------------------------
        if _table_has_bare("orgs", "slug"):
            _ctas_swap(
                table="orgs",
                columns=[
                    ("slug", "TEXT NOT NULL"),
                    ("namespace_kind", "TEXT NOT NULL DEFAULT 'org'"),
                    ("source", "TEXT NOT NULL DEFAULT 'seed'"),
                    ("scope", "TEXT NOT NULL"),
                    ("fullname", "TEXT"),
                    ("details", "TEXT"),
                    ("avatar_url", "TEXT"),
                    ("num_models", "BIGINT"),
                    ("num_datasets", "BIGINT"),
                    ("num_spaces", "BIGINT"),
                    ("num_followers", "BIGINT"),
                    ("raw", "JSON"),
                    (
                        "ingested_at",
                        "TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP",
                    ),
                ],
                select=(
                    "SELECT "
                    f"  CASE WHEN slug LIKE 'https://%' THEN slug ELSE '{ns}' || slug END, "
                    "  namespace_kind, source, scope, fullname, details, avatar_url, "
                    "  num_models, num_datasets, num_spaces, num_followers, raw, ingested_at "
                    "FROM orgs"
                ),
                primary_key=("slug",),
            )

        # --- {models, datasets, spaces}.{repo_id, author} → IRI -------
        # Each entity gets its own URL shape: models live at the namespace
        # root, datasets under /datasets/, spaces under /spaces/. `author`
        # is always the namespace IRI regardless of entity type.
        entity_url_prefix = {
            "models": ns,
            "datasets": ns + "datasets/",
            "spaces": ns + "spaces/",
        }
        for table, prefix in entity_url_prefix.items():
            if not _table_has_bare(table, "repo_id"):
                continue
            cols = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
            col_specs = [(r[1], r[2]) for r in cols]  # (name, type)
            quoted_cols = [name for name, _ in col_specs]
            select_clause = (
                "SELECT "
                + ", ".join(
                    (
                        f"CASE WHEN {c} LIKE 'https://%' THEN {c} "
                        f"     ELSE '{prefix}' || {c} END"
                        if c == "repo_id"
                        else (
                            f"CASE WHEN {c} LIKE 'https://%' OR {c} IS NULL "
                            f"     THEN {c} ELSE '{ns}' || {c} END"
                            if c == "author"
                            else c
                        )
                    )
                    for c in quoted_cols
                )
                + f" FROM {table}"
            )
            # `repo_id` PK is encoded inline; other indexes get recreated
            # by the schema run on the next bootstrap pass (the schema
            # CREATE INDEX IF NOT EXISTS lines are no-ops on tables that
            # don't have them yet).
            col_defs = [
                (name, dtype + (" PRIMARY KEY" if name == "repo_id" else ""))
                for name, dtype in col_specs
            ]
            _ctas_swap(
                table=table,
                columns=col_defs,
                select=select_clause,
            )

        # --- chunks.repo_id (FK) → IRI ----------------------------------
        if _table_has_bare("chunks", "repo_id"):
            cols = conn.execute("PRAGMA table_info('chunks')").fetchall()
            col_specs = [(r[1], r[2]) for r in cols]
            quoted_cols = [name for name, _ in col_specs]
            # entity_type discriminates the URL shape per chunk row.
            select_clause = (
                "SELECT "
                + ", ".join(
                    (
                        "CASE "
                        f"  WHEN {c} LIKE 'https://%' THEN {c} "
                        f"  WHEN entity_type = 'model'   THEN '{ns}' || {c} "
                        f"  WHEN entity_type = 'dataset' THEN '{ns}datasets/' || {c} "
                        f"  WHEN entity_type = 'space'   THEN '{ns}spaces/' || {c} "
                        f"  WHEN entity_type = 'org'     THEN '{ns}' || {c} "
                        f"  ELSE {c} END"
                        if c == "repo_id"
                        else c
                    )
                    for c in quoted_cols
                )
                + " FROM chunks"
            )
            col_defs = [
                (name, dtype + (" PRIMARY KEY" if name == "chunk_id" else ""))
                for name, dtype in col_specs
            ]
            _ctas_swap(table="chunks", columns=col_defs, select=select_clause)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @contextmanager
    def read_only(self) -> Iterator[duckdb.DuckDBPyConnection]:
        ro = duckdb.connect(str(self.db_path), read_only=True)
        try:
            yield ro
        finally:
            ro.close()

    # ---- Upserts ---------------------------------------------------------

    def upsert_org(
        self,
        *,
        slug: str,
        scope: str,
        namespace_kind: str = "org",
        source: str = "seed",
        fullname: str | None = None,
        details: str | None = None,
        avatar_url: str | None = None,
        num_models: int | None = None,
        num_datasets: int | None = None,
        num_spaces: int | None = None,
        num_followers: int | None = None,
        raw: dict[str, Any] | None = None,
    ) -> None:
        from src.index.huggingface.iri import namespace_iri  # noqa: PLC0415

        raw_json = json.dumps(raw, ensure_ascii=False, default=str) if raw is not None else None
        self.connect().execute(
            "INSERT INTO orgs (slug, namespace_kind, source, scope, fullname, "
            "details, avatar_url, num_models, num_datasets, num_spaces, "
            "num_followers, raw, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (slug) DO UPDATE SET "
            "namespace_kind = excluded.namespace_kind, "
            "source = excluded.source, scope = excluded.scope, "
            "fullname = COALESCE(excluded.fullname, orgs.fullname), "
            "details = COALESCE(excluded.details, orgs.details), "
            "avatar_url = COALESCE(excluded.avatar_url, orgs.avatar_url), "
            "num_models = COALESCE(excluded.num_models, orgs.num_models), "
            "num_datasets = COALESCE(excluded.num_datasets, orgs.num_datasets), "
            "num_spaces = COALESCE(excluded.num_spaces, orgs.num_spaces), "
            "num_followers = COALESCE(excluded.num_followers, orgs.num_followers), "
            "raw = COALESCE(excluded.raw, orgs.raw), "
            "ingested_at = excluded.ingested_at",
            [
                namespace_iri(slug), namespace_kind, source, scope, fullname,
                details, avatar_url, num_models, num_datasets, num_spaces,
                num_followers, raw_json, self._now(),
            ],
        )

    def fetch_org(self, slug: str) -> dict[str, Any] | None:
        from src.index.huggingface.iri import namespace_iri  # noqa: PLC0415

        cur = self.connect().execute(
            "SELECT * FROM orgs WHERE slug = ?", [namespace_iri(slug)],
        )
        row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row, strict=False))

    def stream_orgs_for_embedding(
        self,
        *,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield org rows whose namespace text hasn't been embedded yet."""
        sql = (
            "SELECT t.* FROM orgs t WHERE NOT EXISTS ("
            "  SELECT 1 FROM chunks c "
            "  WHERE c.entity_type = 'org' AND c.repo_id = t.slug"
            ")"
        )
        params: list[Any] = []
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        cur = self.connect().execute(sql, params)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        for row in rows:
            yield dict(zip(cols, row, strict=False))

    def list_repo_titles_for_org(self, slug: str) -> dict[str, list[dict[str, Any]]]:
        """Return per-table compact repo info used to compose the org embed text."""
        from src.index.huggingface.iri import namespace_iri  # noqa: PLC0415

        author_iri = namespace_iri(slug)
        out: dict[str, list[dict[str, Any]]] = {}
        for table in ENTITY_TABLES:
            cur = self.connect().execute(
                f"SELECT repo_id, tags, card_data FROM {table} WHERE author = ?",  # noqa: S608
                [author_iri],
            )
            cols = [d[0] for d in cur.description]
            out[table] = [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]
        return out

    def upsert_model(self, row: dict[str, Any], raw: dict[str, Any]) -> None:
        self._upsert_repo(
            table="models",
            cols=(
                "repo_id",
                "author",
                "sha",
                "pipeline_tag",
                "library_name",
                "license",
                "downloads",
                "downloads_all_time",
                "likes",
                "gated",
                "private",
                "created_at",
                "last_modified",
            ),
            json_cols=("tags", "card_data", "base_models", "arxiv_dois"),
            row=row,
            raw=raw,
        )

    def upsert_dataset(self, row: dict[str, Any], raw: dict[str, Any]) -> None:
        self._upsert_repo(
            table="datasets",
            cols=(
                "repo_id",
                "author",
                "sha",
                "license",
                "downloads",
                "downloads_all_time",
                "likes",
                "gated",
                "private",
                "created_at",
                "last_modified",
                "citation_text",
                "paperswithcode_url",
            ),
            json_cols=("tags", "card_data", "dataset_info", "citation_dois"),
            row=row,
            raw=raw,
        )

    def upsert_space(self, row: dict[str, Any], raw: dict[str, Any]) -> None:
        self._upsert_repo(
            table="spaces",
            cols=(
                "repo_id",
                "author",
                "sha",
                "sdk",
                "runtime_stage",
                "hardware",
                "license",
                "likes",
                "created_at",
                "last_modified",
            ),
            json_cols=("tags", "card_data"),
            row=row,
            raw=raw,
        )

    def _upsert_repo(
        self,
        *,
        table: str,
        cols: tuple[str, ...],
        json_cols: tuple[str, ...],
        row: dict[str, Any],
        raw: dict[str, Any],
    ) -> None:
        from src.index.huggingface.iri import namespace_iri  # noqa: PLC0415

        # Normalise repo_id + author to canonical IRI form so direct
        # callers (and unit tests) can keep passing bare ids and still
        # land on the same storage shape as the ingest pipeline.
        row = dict(row)
        if row.get("repo_id"):
            row["repo_id"] = _repo_iri_for_table(table, str(row["repo_id"]))
        if row.get("author"):
            row["author"] = namespace_iri(str(row["author"]))
        all_cols = (*cols, *json_cols, "raw", "ingested_at")
        placeholders = ", ".join(["?"] * len(all_cols))
        col_list = ", ".join(all_cols)
        update_cols = ", ".join(
            f"{c} = excluded.{c}" for c in (*cols[1:], *json_cols, "raw", "ingested_at")
        )
        sql = (
            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT ({cols[0]}) DO UPDATE SET {update_cols}"
        )
        values: list[Any] = [row.get(c) for c in cols]
        for jc in json_cols:
            payload = row.get(jc)
            values.append(json.dumps(payload, ensure_ascii=False, default=str) if payload is not None else None)
        values.append(json.dumps(raw, ensure_ascii=False, default=str))
        values.append(self._now())
        self.connect().execute(sql, values)

    def upsert_chunk(
        self,
        *,
        chunk_id: str,
        entity_type: str,
        repo_id: str,
        chunk_index: int,
        text: str,
        token_count: int,
        vector_id: str,
    ) -> None:
        from src.index.huggingface.iri import iri_for_entity_type  # noqa: PLC0415

        self.connect().execute(
            "INSERT INTO chunks "
            "(chunk_id, entity_type, repo_id, chunk_index, text, "
            "token_count, vector_id) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (chunk_id) DO UPDATE SET "
            "text = excluded.text, token_count = excluded.token_count, "
            "vector_id = excluded.vector_id, embedded_at = now()",
            [
                chunk_id,
                entity_type,
                iri_for_entity_type(entity_type, repo_id),
                chunk_index,
                text,
                token_count,
                vector_id,
            ],
        )

    # ---- Reads -----------------------------------------------------------

    def count(self, table: str) -> int:
        result = self.connect().execute(f"SELECT count(*) FROM {table}").fetchone()
        return int(result[0]) if result else 0

    def stream_rows_for_embedding(
        self,
        entity_table: str,
        *,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield repo rows whose card hasn't been embedded yet."""
        if entity_table not in ENTITY_TABLES:
            message = f"Unknown entity table: {entity_table}"
            raise ValueError(message)
        # Map plural → singular for chunks.entity_type comparison.
        singular = {"models": "model", "datasets": "dataset", "spaces": "space"}[
            entity_table
        ]
        sql = (
            f"SELECT t.* FROM {entity_table} t "  # noqa: S608 - table guarded above
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM chunks c "
            "  WHERE c.entity_type = ? AND c.repo_id = t.repo_id"
            ")"
        )
        params: list[Any] = [singular]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        # Materialize upfront: the embed pipeline calls `upsert_chunk` on the
        # same connection inside `flush()`, which replaces the connection's
        # active cursor and clobbers our SELECT result set.
        cur = self.connect().execute(sql, params)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        for row in rows:
            yield dict(zip(cols, row, strict=False))

    def repo_sha(self, entity_table: str, repo_id: str) -> str | None:
        """Return the stored `sha` for a repo, or None if the repo isn't in the table."""
        if entity_table not in ENTITY_TABLES:
            return None
        cur = self.connect().execute(
            f"SELECT sha FROM {entity_table} WHERE repo_id = ?",  # noqa: S608
            [_repo_iri_for_table(entity_table, repo_id)],
        )
        row = cur.fetchone()
        return row[0] if row else None

    def fetch_repo(self, entity_table: str, repo_id: str) -> dict[str, Any] | None:
        if entity_table not in ENTITY_TABLES:
            return None
        cur = self.connect().execute(
            f"SELECT * FROM {entity_table} WHERE repo_id = ?",  # noqa: S608
            [_repo_iri_for_table(entity_table, repo_id)],
        )
        row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row, strict=False))

    @staticmethod
    def _now() -> str:
        return datetime.now(tz=timezone.utc).isoformat()
