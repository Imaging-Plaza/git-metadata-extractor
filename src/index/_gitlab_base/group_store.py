"""DuckDB lifecycle, schema bootstrap, and upsert helpers for the GitLab groups index."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb

if TYPE_CHECKING:
    from collections.abc import Iterator

    from src.index._gitlab_base.models import GitLabGroupRecord

LOGGER = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).parent / "group_schema.sql"

ENTITY_TYPE = "groups"
ID_COLUMN = "group_id"

EMBEDDABLE_ENTITY_TYPES = {"groups"}


def _load_schema_sql() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


class GitLabGroupStore:
    """Thin DuckDB wrapper for the GitLab groups schema. `bootstrap()` is idempotent."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: duckdb.DuckDBPyConnection | None = None

    @classmethod
    def open(cls, db_path: Path) -> GitLabGroupStore:
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

    # ---- Upserts ---------------------------------------------------------

    def upsert_group(self, record: GitLabGroupRecord) -> None:
        sql = (
            "INSERT INTO groups "
            "(group_id, host, full_path, name, description, visibility, "
            " parent, web_url, raw, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (group_id) DO UPDATE SET "
            "  host = excluded.host, "
            "  full_path = excluded.full_path, "
            "  name = excluded.name, "
            "  description = excluded.description, "
            "  visibility = excluded.visibility, "
            "  parent = excluded.parent, "
            "  web_url = excluded.web_url, "
            "  raw = excluded.raw, "
            "  ingested_at = excluded.ingested_at"
        )
        self.connect().execute(
            sql,
            [
                record.group_id,
                record.host,
                record.full_path,
                record.name,
                record.description,
                record.visibility,
                record.parent,
                record.web_url,
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

    def fetch_group(self, group_id: str) -> dict[str, Any] | None:
        cur = self.connect().execute(
            "SELECT * FROM groups WHERE group_id = ?",
            [group_id],
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
        """Yield group rows that need embedding (no chunks yet)."""
        if entity_type not in EMBEDDABLE_ENTITY_TYPES:
            message = f"Unknown entity_type: {entity_type}"
            raise ValueError(message)
        sql = (
            "SELECT t.* FROM groups t "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM chunks c "
            "  WHERE c.entity_type = ? AND c.entity_id = t.group_id"
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
