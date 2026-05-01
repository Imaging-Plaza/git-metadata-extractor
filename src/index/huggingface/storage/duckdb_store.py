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
                slug, namespace_kind, source, scope, fullname,
                details, avatar_url, num_models, num_datasets, num_spaces,
                num_followers, raw_json, self._now(),
            ],
        )

    def fetch_org(self, slug: str) -> dict[str, Any] | None:
        cur = self.connect().execute("SELECT * FROM orgs WHERE slug = ?", [slug])
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
        out: dict[str, list[dict[str, Any]]] = {}
        for table in ENTITY_TABLES:
            cur = self.connect().execute(
                f"SELECT repo_id, tags, card_data FROM {table} WHERE author = ?",  # noqa: S608
                [slug],
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
            json_cols=("tags", "card_data", "base_models"),
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
            ),
            json_cols=("tags", "card_data", "dataset_info"),
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
                repo_id,
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

    def fetch_repo(self, entity_table: str, repo_id: str) -> dict[str, Any] | None:
        if entity_table not in ENTITY_TABLES:
            return None
        cur = self.connect().execute(
            f"SELECT * FROM {entity_table} WHERE repo_id = ?",  # noqa: S608
            [repo_id],
        )
        row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row, strict=False))

    @staticmethod
    def _now() -> str:
        return datetime.now(tz=timezone.utc).isoformat()
