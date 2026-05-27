"""DuckDB lifecycle, schema bootstrap, and upsert helpers for Zenodo."""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb

from src.index.zenodo.paths import get_zenodo_paths

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

LOGGER = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

EMBEDDABLE_ENTITY_TYPES = {"records"}


def _load_schema_sql() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


class ZenodoStore:
    """Thin DuckDB wrapper for the Zenodo schema. `bootstrap()` is idempotent."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: duckdb.DuckDBPyConnection | None = None

    @classmethod
    def open(cls, db_path: Path | None = None) -> ZenodoStore:
        if db_path is None:
            db_path = get_zenodo_paths().duckdb_path
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
        # Migration: existing DBs created before `concept_recid` existed must
        # gain the column BEFORE schema.sql runs (schema.sql creates an index
        # on it). The backfill UPDATE must also run BEFORE the index is
        # created — DuckDB has hit internal errors when an UPDATE rewrites
        # every row of a freshly-created index in the same transaction.
        records_exists = (
            conn.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='main' AND table_name='records'",
            ).fetchone()
            is not None
        )
        if records_exists:
            cols = {
                r[1]
                for r in conn.execute("PRAGMA table_info('records')").fetchall()
            }
            if "concept_recid" not in cols:
                conn.execute("ALTER TABLE records ADD COLUMN concept_recid TEXT")
                conn.execute(
                    "UPDATE records SET concept_recid = "
                    "  CAST(json_extract_string(raw, '$.conceptrecid') AS TEXT) "
                    "WHERE raw IS NOT NULL "
                    "  AND json_extract_string(raw, '$.conceptrecid') IS NOT NULL",
                )
            # Stats + version + timestamp columns. Added in the
            # `feat/zenodo-stats-and-version-columns` PR; existing DBs
            # need to ALTER TABLE before the CREATE TABLE IF NOT EXISTS
            # in schema.sql (a no-op since the table already exists)
            # can pick them up. Backfill from `raw` for any pre-existing
            # rows whose new column is still NULL.
            _ADDED_RECORD_COLUMNS: tuple[tuple[str, str, str], ...] = (
                # (column, DDL type, JSONPath into `raw` for backfill)
                ("concept_doi", "TEXT", "$.conceptdoi"),
                ("version", "TEXT", "$.metadata.version"),
                ("revision", "INTEGER", "$.revision"),
                ("created_at", "TIMESTAMP", "$.created"),
                ("updated_at", "TIMESTAMP", "$.updated"),
                ("views", "BIGINT", "$.stats.views"),
                ("unique_views", "BIGINT", "$.stats.unique_views"),
                ("downloads", "BIGINT", "$.stats.downloads"),
                ("unique_downloads", "BIGINT", "$.stats.unique_downloads"),
                ("version_views", "BIGINT", "$.stats.version_views"),
                (
                    "version_unique_views",
                    "BIGINT",
                    "$.stats.version_unique_views",
                ),
                (
                    "version_downloads",
                    "BIGINT",
                    "$.stats.version_downloads",
                ),
                (
                    "version_unique_downloads",
                    "BIGINT",
                    "$.stats.version_unique_downloads",
                ),
            )
            for col, ddl_type, json_path in _ADDED_RECORD_COLUMNS:
                if col in cols:
                    continue
                conn.execute(f"ALTER TABLE records ADD COLUMN {col} {ddl_type}")
                # Backfill from `raw`. Cast appropriately by type — DuckDB
                # accepts TEXT for json_extract_string + autocoerces from
                # string for the BIGINT / TIMESTAMP cases.
                if ddl_type == "TEXT":
                    cast_expr = (
                        f"CAST(json_extract_string(raw, '{json_path}') AS TEXT)"
                    )
                elif ddl_type == "INTEGER":
                    cast_expr = (
                        f"TRY_CAST(json_extract_string(raw, '{json_path}') AS INTEGER)"
                    )
                elif ddl_type == "BIGINT":
                    cast_expr = (
                        f"TRY_CAST(json_extract_string(raw, '{json_path}') AS BIGINT)"
                    )
                elif ddl_type == "TIMESTAMP":
                    cast_expr = (
                        f"TRY_CAST(json_extract_string(raw, '{json_path}') AS TIMESTAMP)"
                    )
                else:
                    cast_expr = f"json_extract_string(raw, '{json_path}')"
                conn.execute(
                    f"UPDATE records SET {col} = {cast_expr} "
                    "WHERE raw IS NOT NULL AND "
                    f"     json_extract_string(raw, '{json_path}') IS NOT NULL",
                )
        conn.execute(_load_schema_sql())
        # IRI migration for the link tables — see the note at the
        # bottom of schema.sql. UPDATE on indexed bulk columns has
        # tripped DuckDB internal index errors on tables this size,
        # so we drop-and-rebuild via CTAS.
        self._migrate_link_tables_to_iri()
        # DOIs → canonical doi.org URL form. Idempotent.
        self._migrate_dois_to_url()
        # Descriptions whose stored value still carries HTML get
        # re-cleaned through the iterative stripper, sourcing from
        # `raw.metadata.description`. Idempotent (skips rows that
        # are already clean).
        self._migrate_descriptions_strip_html()

    def _migrate_descriptions_strip_html(self) -> None:
        """Re-clean any `records.description` that still contains HTML.

        Pre-PR rows were stripped with a single BeautifulSoup pass that
        unescaped inner content without re-stripping it (so the stored
        text retained the now-decoded tags). For each row whose
        description still has `<…>`, re-derive from
        `raw.metadata.description` via `_strip_html` and write back.
        Bounded by the row count of records actually containing HTML —
        fast in practice (~hundreds of rows for our deployment, not all
        6.7k).
        """
        from src.index.zenodo.ingest.records import _strip_html  # noqa: PLC0415

        conn = self.connect()
        candidates = conn.execute(
            "SELECT zenodo_id, raw FROM records "
            "WHERE description LIKE '%<%>%' OR description LIKE '%&lt;%'",
        ).fetchall()
        if not candidates:
            return
        cleaned = 0
        for zenodo_id, raw_payload in candidates:
            if isinstance(raw_payload, str):
                try:
                    raw = json.loads(raw_payload)
                except json.JSONDecodeError:
                    continue
            else:
                raw = raw_payload
            if not isinstance(raw, dict):
                continue
            metadata = raw.get("metadata")
            source = (
                metadata.get("description")
                if isinstance(metadata, dict)
                else None
            )
            new_desc = _strip_html(source)
            conn.execute(
                "UPDATE records SET description = ? WHERE zenodo_id = ?",
                [new_desc, zenodo_id],
            )
            cleaned += 1
        LOGGER.info("zenodo: re-cleaned %d record descriptions", cleaned)

    def _migrate_dois_to_url(self) -> None:
        """Promote `records.doi` / `records.concept_doi` to the
        `https://doi.org/<bare>` URL form. Idempotent — rows already in
        URL form match no WHERE clause.
        """
        conn = self.connect()
        # Detect schema readiness — `concept_doi` was added by the
        # stats-and-version PR; older DBs without it skip silently.
        cols = {
            r[1] for r in conn.execute("PRAGMA table_info('records')").fetchall()
        }
        if "doi" in cols:
            conn.execute(
                "UPDATE records "
                "   SET doi = 'https://doi.org/' || doi "
                " WHERE doi IS NOT NULL "
                "   AND doi NOT LIKE 'https://doi.org/%' "
                "   AND doi NOT LIKE 'https://dx.doi.org/%'",
            )
            # Normalise the legacy `dx.doi.org` host while we're here.
            conn.execute(
                "UPDATE records "
                "   SET doi = 'https://doi.org/' || "
                "             SUBSTRING(doi, LENGTH('https://dx.doi.org/') + 1) "
                " WHERE doi LIKE 'https://dx.doi.org/%'",
            )
        if "concept_doi" in cols:
            conn.execute(
                "UPDATE records "
                "   SET concept_doi = 'https://doi.org/' || concept_doi "
                " WHERE concept_doi IS NOT NULL "
                "   AND concept_doi NOT LIKE 'https://doi.org/%' "
                "   AND concept_doi NOT LIKE 'https://dx.doi.org/%'",
            )
            conn.execute(
                "UPDATE records "
                "   SET concept_doi = 'https://doi.org/' || "
                "             SUBSTRING(concept_doi, LENGTH('https://dx.doi.org/') + 1) "
                " WHERE concept_doi LIKE 'https://dx.doi.org/%'",
            )

    def _migrate_link_tables_to_iri(self) -> None:
        """CTAS-swap link/chunk tables with `record_id`/`entity_id` →
        IRI. Idempotent: each step checks for bare-id rows first and
        is a no-op when everything is already migrated.
        """
        conn = self.connect()
        record_prefix = "https://zenodo.org/records/"
        community_prefix = "https://zenodo.org/communities/"

        def _has_bare(table: str, col: str, extra: str = "") -> bool:
            row = conn.execute(
                f"SELECT 1 FROM {table} WHERE {col} NOT LIKE 'https://%' "
                f"{extra} LIMIT 1",
            ).fetchone()
            return row is not None

        def _ctas_swap(
            *, table: str, columns: list[tuple[str, str]],
            select: str, primary_key: tuple[str, ...] | None = None,
        ) -> None:
            """Rewrite `table` via CREATE TABLE _new + INSERT + DROP + RENAME."""
            new_table = f"{table}__iri_migrate"
            conn.execute(f"DROP TABLE IF EXISTS {new_table}")
            col_defs = ", ".join(f"{name} {dtype}" for name, dtype in columns)
            if primary_key:
                col_defs += f", PRIMARY KEY ({', '.join(primary_key)})"
            conn.execute(f"CREATE TABLE {new_table} ({col_defs})")
            conn.execute(f"INSERT INTO {new_table} {select}")
            conn.execute(f"DROP TABLE {table}")
            conn.execute(f"ALTER TABLE {new_table} RENAME TO {table}")

        if _has_bare("record_creators", "record_id"):
            _ctas_swap(
                table="record_creators",
                columns=[
                    ("record_id", "TEXT NOT NULL"),
                    ("creator_key", "TEXT NOT NULL"),
                    ("position", "INTEGER"),
                ],
                select=(
                    "SELECT CASE WHEN record_id LIKE 'https://%' THEN record_id "
                    f"            ELSE '{record_prefix}' || record_id END, "
                    "       creator_key, position "
                    "FROM record_creators"
                ),
                primary_key=("record_id", "creator_key"),
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_record_creators_ck "
                "ON record_creators (creator_key)",
            )

        if _has_bare("record_communities", "record_id") or _has_bare(
            "record_communities", "community_id",
        ):
            _ctas_swap(
                table="record_communities",
                columns=[
                    ("record_id", "TEXT NOT NULL"),
                    ("community_id", "TEXT NOT NULL"),
                ],
                select=(
                    "SELECT "
                    "  CASE WHEN record_id LIKE 'https://%' THEN record_id "
                    f"       ELSE '{record_prefix}' || record_id END, "
                    "  CASE WHEN community_id LIKE 'https://%' THEN community_id "
                    f"       ELSE '{community_prefix}' || community_id END "
                    "FROM record_communities"
                ),
                primary_key=("record_id", "community_id"),
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_record_comm_cid "
                "ON record_communities (community_id)",
            )

        if _has_bare("files", "record_id"):
            _ctas_swap(
                table="files",
                columns=[
                    ("record_id", "TEXT NOT NULL"),
                    ("file_key", "TEXT NOT NULL"),
                    ("file_id", "TEXT"),
                    ("size_bytes", "BIGINT"),
                    ("checksum", "TEXT"),
                    ("download_url", "TEXT"),
                ],
                select=(
                    "SELECT "
                    "  CASE WHEN record_id LIKE 'https://%' THEN record_id "
                    f"       ELSE '{record_prefix}' || record_id END, "
                    "  file_key, file_id, size_bytes, checksum, download_url "
                    "FROM files"
                ),
                primary_key=("record_id", "file_key"),
            )

        if _has_bare("chunks", "entity_id", extra="AND entity_type = 'records'"):
            _ctas_swap(
                table="chunks",
                columns=[
                    ("chunk_id", "TEXT PRIMARY KEY"),
                    ("entity_type", "TEXT NOT NULL"),
                    ("entity_id", "TEXT NOT NULL"),
                    ("chunk_index", "INTEGER NOT NULL"),
                    ("text", "TEXT NOT NULL"),
                    ("token_count", "INTEGER NOT NULL"),
                    ("vector_id", "TEXT NOT NULL"),
                    (
                        "embedded_at",
                        "TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP",
                    ),
                ],
                select=(
                    "SELECT chunk_id, entity_type, "
                    "  CASE WHEN entity_type = 'records' "
                    "       AND entity_id NOT LIKE 'https://%' "
                    f"       THEN '{record_prefix}' || entity_id "
                    "       ELSE entity_id END, "
                    "  chunk_index, text, token_count, vector_id, embedded_at "
                    "FROM chunks"
                ),
                primary_key=None,  # chunk_id PK is inline above
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_entity "
                "ON chunks (entity_type, entity_id)",
            )

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

    def upsert_record(self, row: dict[str, Any], raw: dict[str, Any]) -> None:
        # `community_ids` and `primary_community_id` are denormalised
        # community membership baked onto the record itself so
        # downstream queries can filter without joining
        # `record_communities`. The caller passes the community we're
        # currently crawling under as `primary_community_id`, plus the
        # full set of communities the record's raw payload mentions
        # as `community_ids` (deduped).
        community_ids = row.get("community_ids")
        if not isinstance(community_ids, list):
            community_ids = []
        sql = (
            "INSERT INTO records "
            "(zenodo_id, concept_recid, doi, concept_doi, title, description, "
            " publication_date, resource_type, access_right, license_id, "
            " version, revision, created_at, updated_at, "
            " views, unique_views, downloads, unique_downloads, "
            " version_views, version_unique_views, version_downloads, "
            " version_unique_downloads, keywords_json, "
            " community_ids, primary_community_id, raw, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "        ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (zenodo_id) DO UPDATE SET "
            "  concept_recid = excluded.concept_recid, "
            "  doi = excluded.doi, concept_doi = excluded.concept_doi, "
            "  title = excluded.title, description = excluded.description, "
            "  publication_date = excluded.publication_date, "
            "  resource_type = excluded.resource_type, "
            "  access_right = excluded.access_right, "
            "  license_id = excluded.license_id, "
            "  version = excluded.version, revision = excluded.revision, "
            "  created_at = excluded.created_at, "
            "  updated_at = excluded.updated_at, "
            "  views = excluded.views, unique_views = excluded.unique_views, "
            "  downloads = excluded.downloads, "
            "  unique_downloads = excluded.unique_downloads, "
            "  version_views = excluded.version_views, "
            "  version_unique_views = excluded.version_unique_views, "
            "  version_downloads = excluded.version_downloads, "
            "  version_unique_downloads = excluded.version_unique_downloads, "
            "  keywords_json = excluded.keywords_json, "
            "  community_ids = excluded.community_ids, "
            "  primary_community_id = COALESCE("
            "      records.primary_community_id, excluded.primary_community_id), "
            "  raw = excluded.raw, ingested_at = excluded.ingested_at"
        )
        self.connect().execute(
            sql,
            [
                row["zenodo_id"],
                row.get("concept_recid"),
                row.get("doi"),
                row.get("concept_doi"),
                row.get("title"),
                row.get("description"),
                row.get("publication_date"),
                row.get("resource_type"),
                row.get("access_right"),
                row.get("license_id"),
                row.get("version"),
                row.get("revision"),
                row.get("created_at"),
                row.get("updated_at"),
                row.get("views"),
                row.get("unique_views"),
                row.get("downloads"),
                row.get("unique_downloads"),
                row.get("version_views"),
                row.get("version_unique_views"),
                row.get("version_downloads"),
                row.get("version_unique_downloads"),
                json.dumps(row.get("keywords") or [], ensure_ascii=False),
                json.dumps(community_ids, ensure_ascii=False),
                row.get("primary_community_id"),
                json.dumps(raw, ensure_ascii=False),
                self._now(),
            ],
        )

    def upsert_creator(self, row: dict[str, Any], raw: dict[str, Any]) -> None:
        sql = (
            "INSERT INTO creators "
            "(creator_key, display_name, orcid, affiliation, raw, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (creator_key) DO UPDATE SET "
            "  display_name = excluded.display_name, "
            "  orcid = excluded.orcid, "
            "  affiliation = excluded.affiliation, "
            "  raw = excluded.raw, ingested_at = excluded.ingested_at"
        )
        self.connect().execute(
            sql,
            [
                row["creator_key"],
                row.get("display_name"),
                row.get("orcid"),
                row.get("affiliation"),
                json.dumps(raw, ensure_ascii=False),
                self._now(),
            ],
        )

    def upsert_record_creators(
        self,
        record_id: str,
        creator_positions: Iterable[tuple[str, int]],
    ) -> None:
        conn = self.connect()
        for creator_key, position in creator_positions:
            conn.execute(
                "INSERT INTO record_creators (record_id, creator_key, position) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT (record_id, creator_key) DO UPDATE SET "
                "position = LEAST(record_creators.position, excluded.position)",
                [record_id, creator_key, position],
            )

    def upsert_community(self, row: dict[str, Any], raw: dict[str, Any]) -> None:
        sql = (
            "INSERT INTO communities (community_id, title, raw, ingested_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT (community_id) DO UPDATE SET "
            "  title = excluded.title, raw = excluded.raw, "
            "  ingested_at = excluded.ingested_at"
        )
        self.connect().execute(
            sql,
            [
                row["community_id"],
                row.get("title"),
                json.dumps(raw, ensure_ascii=False),
                self._now(),
            ],
        )

    def ensure_community(self, community_id: str) -> None:
        """Insert a stub `communities` row if the id is not already
        present — never overwrites an existing row.

        Records reference communities the crawl never bootstrapped
        metadata for. Without this, those ids appear in
        `record_communities` but are absent from the `communities`
        master table (orphan references). `ON CONFLICT DO NOTHING`
        guarantees a stub never clobbers real metadata written by
        `upsert_community`, regardless of ingest order.
        """
        self.connect().execute(
            "INSERT INTO communities (community_id, title, raw, ingested_at) "
            "VALUES (?, NULL, NULL, ?) "
            "ON CONFLICT (community_id) DO NOTHING",
            [community_id, self._now()],
        )

    def upsert_record_communities(
        self,
        record_id: str,
        community_ids: Iterable[str],
    ) -> None:
        conn = self.connect()
        for cid in community_ids:
            conn.execute(
                "INSERT INTO record_communities (record_id, community_id) "
                "VALUES (?, ?) ON CONFLICT DO NOTHING",
                [record_id, cid],
            )

    def upsert_file(self, row: dict[str, Any]) -> None:
        sql = (
            "INSERT INTO files "
            "(record_id, file_key, file_id, size_bytes, checksum, download_url) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (record_id, file_key) DO UPDATE SET "
            "  file_id = excluded.file_id, "
            "  size_bytes = excluded.size_bytes, "
            "  checksum = excluded.checksum, "
            "  download_url = excluded.download_url"
        )
        self.connect().execute(
            sql,
            [
                row["record_id"],
                row["file_key"],
                row.get("file_id"),
                row.get("size_bytes"),
                row.get("checksum"),
                row.get("download_url"),
            ],
        )

    def upsert_chunk(
        self,
        *,
        chunk_id: str,
        entity_type: str,
        entity_id: str,
        chunk_index: int,
        text: str,
        token_count: int,
        vector_id: str,
    ) -> None:
        self.connect().execute(
            "INSERT INTO chunks "
            "(chunk_id, entity_type, entity_id, chunk_index, text, "
            "token_count, vector_id) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (chunk_id) DO UPDATE SET "
            "text = excluded.text, token_count = excluded.token_count, "
            "vector_id = excluded.vector_id, embedded_at = now()",
            [chunk_id, entity_type, entity_id, chunk_index, text, token_count, vector_id],
        )

    # ---- Reads -----------------------------------------------------------

    def count(self, table: str) -> int:
        result = self.connect().execute(f"SELECT count(*) FROM {table}").fetchone()
        return int(result[0]) if result else 0

    def existing_record_ids(self, zenodo_ids: list[str]) -> set[str]:
        """Return the subset of `zenodo_ids` already known to the store.

        Input is a list of bare numeric ids (`18314844`) — discovery
        sources extract those from search-result text. Internally we
        compare against:

        - `records.zenodo_id`, which post-IRI-migration is the URL form
          `https://zenodo.org/records/18314844` — so we promote the
          input before SELECT.
        - `records.concept_recid`, which stays a bare numeric id.

        Returned set is in the original bare-id shape so callers can
        diff against their own bare-id discovery set.
        """
        if not zenodo_ids:
            return set()
        from src.index.zenodo.iri import parse_record_id, record_iri  # noqa: PLC0415

        iri_form = [record_iri(z) for z in zenodo_ids]
        placeholders = ",".join(["?"] * len(zenodo_ids))
        cur = self.connect().execute(
            f"SELECT zenodo_id FROM records WHERE zenodo_id IN ({placeholders}) "
            f"UNION "
            f"SELECT concept_recid FROM records "
            f"WHERE concept_recid IN ({placeholders})",
            [*iri_form, *zenodo_ids],
        )
        found: set[str] = set()
        for (value,) in cur.fetchall():
            if value is None:
                continue
            bare = parse_record_id(str(value))
            if bare:
                found.add(bare)
        return found

    def fetch_record(self, zenodo_id: str) -> dict[str, Any] | None:
        from src.index.zenodo.iri import record_iri  # noqa: PLC0415

        cur = self.connect().execute(
            "SELECT * FROM records WHERE zenodo_id = ?",
            [record_iri(zenodo_id)],
        )
        row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row, strict=False))

    def fetch_record_by_concept(self, concept_recid: str) -> dict[str, Any] | None:
        """Find any version-record under the given concept_recid.

        When a citation references the concept (parent) ID, our store only
        keeps the canonical version-record. This returns the most recent
        ingested version so callers can still resolve the citation.
        """
        cur = self.connect().execute(
            "SELECT * FROM records WHERE concept_recid = ? "
            "ORDER BY ingested_at DESC LIMIT 1",
            [concept_recid],
        )
        row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row, strict=False))

    def stream_rows_for_embedding(
        self,
        entity_type: str,
        *,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield rows that need embedding (no chunks yet)."""
        if entity_type not in EMBEDDABLE_ENTITY_TYPES:
            message = f"Unknown entity_type: {entity_type}"
            raise ValueError(message)
        # records.zenodo_id is the entity_id for the chunks table.
        sql = (
            "SELECT t.* FROM records t "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM chunks c "
            "  WHERE c.entity_type = ? AND c.entity_id = t.zenodo_id"
            ")"
        )
        params: list[Any] = [entity_type]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        cur = self.connect().execute(sql, params)
        cols = [d[0] for d in cur.description]
        # Materialize the full result up front: DuckDB's single-connection
        # model lets writes mid-iteration affect the NOT EXISTS predicate
        # if we stream lazily, so each upsert_chunk call would otherwise
        # filter out subsequent rows.
        rows = cur.fetchall()
        for row in rows:
            yield dict(zip(cols, row, strict=False))

    @staticmethod
    def _now() -> str:
        return datetime.now(tz=timezone.utc).isoformat()
