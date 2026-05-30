"""DuckDB lifecycle, schema bootstrap, and upsert helpers for the Docker Hub index."""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb

from src.index.dockerhub.paths import get_dockerhub_paths

if TYPE_CHECKING:
    from collections.abc import Iterator

    from src.index.dockerhub.models import DockerhubRepoRecord

LOGGER = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

EMBEDDABLE_ENTITY_TYPES = {"images"}


def _load_schema_sql() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


class DockerhubStore:
    """Thin DuckDB wrapper for the Docker Hub schema. `bootstrap()` is idempotent."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: duckdb.DuckDBPyConnection | None = None

    @classmethod
    def open(cls, db_path: Path | None = None) -> DockerhubStore:
        if db_path is None:
            db_path = get_dockerhub_paths().duckdb_path
        store = cls(db_path)
        store.bootstrap()
        return store

    def connect(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = duckdb.connect(str(self.db_path))
        return self._conn

    def bootstrap(self) -> None:
        self.connect().execute(_load_schema_sql())

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

    def upsert_image(self, record: DockerhubRepoRecord) -> None:
        sql = (
            "INSERT INTO images "
            "(repo_id, namespace, name, description, full_description, "
            " is_official, is_automated, is_private, star_count, pull_count, "
            " status, last_updated, date_registered, tags, raw, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (repo_id) DO UPDATE SET "
            "  namespace = excluded.namespace, name = excluded.name, "
            "  description = excluded.description, "
            "  full_description = excluded.full_description, "
            "  is_official = excluded.is_official, "
            "  is_automated = excluded.is_automated, "
            "  is_private = excluded.is_private, "
            "  star_count = excluded.star_count, "
            "  pull_count = excluded.pull_count, "
            "  status = excluded.status, "
            "  last_updated = excluded.last_updated, "
            "  date_registered = excluded.date_registered, "
            "  tags = excluded.tags, raw = excluded.raw, "
            "  ingested_at = excluded.ingested_at"
        )
        self.connect().execute(
            sql,
            [
                record.repo_id,
                record.namespace,
                record.name,
                record.description,
                record.full_description,
                record.is_official,
                record.is_automated,
                record.is_private,
                record.star_count,
                record.pull_count,
                record.status,
                record.last_updated,
                record.date_registered,
                json.dumps(record.tags, ensure_ascii=False),
                json.dumps(record.raw, ensure_ascii=False, default=str),
                self._now(),
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

    def fetch_image(self, repo_id: str) -> dict[str, Any] | None:
        cur = self.connect().execute(
            "SELECT * FROM images WHERE repo_id = ?",
            [repo_id],
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
        """Yield image rows that need embedding (no chunks yet)."""
        if entity_type not in EMBEDDABLE_ENTITY_TYPES:
            message = f"Unknown entity_type: {entity_type}"
            raise ValueError(message)
        sql = (
            "SELECT t.* FROM images t "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM chunks c "
            "  WHERE c.entity_type = ? AND c.entity_id = t.repo_id"
            ")"
        )
        params: list[Any] = [entity_type]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        cur = self.connect().execute(sql, params)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        for row in rows:
            yield dict(zip(cols, row, strict=False))

    @staticmethod
    def _now() -> str:
        return datetime.now(tz=timezone.utc).isoformat()
