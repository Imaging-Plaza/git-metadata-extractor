"""DuckDB lifecycle, schema bootstrap, and upsert helpers for RenkuLab."""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb

from src.index.renkulab.paths import get_renkulab_paths

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

LOGGER = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

EMBEDDABLE_ENTITY_TYPES = {"projects", "groups", "users", "data_connectors"}

# Map entity_type → (table, primary key column).
_ENTITY_PK: dict[str, tuple[str, str]] = {
    "projects": ("projects", "project_id"),
    "groups": ("groups", "group_id"),
    "users": ("users", "user_id"),
    "data_connectors": ("data_connectors", "data_connector_id"),
}


def _load_schema_sql() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


class RenkulabStore:
    """Thin DuckDB wrapper for the RenkuLab schema. `bootstrap()` is idempotent."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: duckdb.DuckDBPyConnection | None = None

    @classmethod
    def open(cls, db_path: Path | None = None) -> RenkulabStore:
        if db_path is None:
            db_path = get_renkulab_paths().duckdb_path
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

    def upsert_project(self, row: dict[str, Any], raw: dict[str, Any]) -> None:
        sql = (
            "INSERT INTO projects "
            "(project_id, slug, name, namespace, path, description, visibility, "
            " is_template, keywords_json, repositories_json, created_by, "
            " creation_date, updated_at, raw, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (project_id) DO UPDATE SET "
            "  slug = excluded.slug, name = excluded.name, "
            "  namespace = excluded.namespace, path = excluded.path, "
            "  description = excluded.description, "
            "  visibility = excluded.visibility, "
            "  is_template = excluded.is_template, "
            "  keywords_json = excluded.keywords_json, "
            "  repositories_json = excluded.repositories_json, "
            "  created_by = excluded.created_by, "
            "  creation_date = excluded.creation_date, "
            "  updated_at = excluded.updated_at, "
            "  raw = excluded.raw, ingested_at = excluded.ingested_at"
        )
        self.connect().execute(
            sql,
            [
                row["project_id"],
                row.get("slug"),
                row.get("name"),
                row.get("namespace"),
                row.get("path"),
                row.get("description"),
                row.get("visibility"),
                row.get("is_template"),
                json.dumps(row.get("keywords") or [], ensure_ascii=False),
                json.dumps(row.get("repositories") or [], ensure_ascii=False),
                row.get("created_by"),
                row.get("creation_date"),
                row.get("updated_at"),
                json.dumps(raw, ensure_ascii=False),
                self._now(),
            ],
        )

    def upsert_group(self, row: dict[str, Any], raw: dict[str, Any]) -> None:
        sql = (
            "INSERT INTO groups "
            "(group_id, slug, name, description, created_by, creation_date, "
            " raw, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (group_id) DO UPDATE SET "
            "  slug = excluded.slug, name = excluded.name, "
            "  description = excluded.description, "
            "  created_by = excluded.created_by, "
            "  creation_date = excluded.creation_date, "
            "  raw = excluded.raw, ingested_at = excluded.ingested_at"
        )
        self.connect().execute(
            sql,
            [
                row["group_id"],
                row.get("slug"),
                row.get("name"),
                row.get("description"),
                row.get("created_by"),
                row.get("creation_date"),
                json.dumps(raw, ensure_ascii=False),
                self._now(),
            ],
        )

    def upsert_user(self, row: dict[str, Any], raw: dict[str, Any]) -> None:
        sql = (
            "INSERT INTO users "
            "(user_id, slug, path, first_name, last_name, raw, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (user_id) DO UPDATE SET "
            "  slug = excluded.slug, path = excluded.path, "
            "  first_name = excluded.first_name, "
            "  last_name = excluded.last_name, "
            "  raw = excluded.raw, ingested_at = excluded.ingested_at"
        )
        self.connect().execute(
            sql,
            [
                row["user_id"],
                row.get("slug"),
                row.get("path"),
                row.get("first_name"),
                row.get("last_name"),
                json.dumps(raw, ensure_ascii=False),
                self._now(),
            ],
        )

    def upsert_data_connector(
        self,
        row: dict[str, Any],
        raw: dict[str, Any],
    ) -> None:
        sql = (
            "INSERT INTO data_connectors "
            "(data_connector_id, slug, name, namespace, path, description, "
            " visibility, storage_type, storage_provider, source_path, "
            " target_path, readonly, keywords_json, created_by, "
            " creation_date, raw, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (data_connector_id) DO UPDATE SET "
            "  slug = excluded.slug, name = excluded.name, "
            "  namespace = excluded.namespace, path = excluded.path, "
            "  description = excluded.description, "
            "  visibility = excluded.visibility, "
            "  storage_type = excluded.storage_type, "
            "  storage_provider = excluded.storage_provider, "
            "  source_path = excluded.source_path, "
            "  target_path = excluded.target_path, "
            "  readonly = excluded.readonly, "
            "  keywords_json = excluded.keywords_json, "
            "  created_by = excluded.created_by, "
            "  creation_date = excluded.creation_date, "
            "  raw = excluded.raw, ingested_at = excluded.ingested_at"
        )
        self.connect().execute(
            sql,
            [
                row["data_connector_id"],
                row.get("slug"),
                row.get("name"),
                row.get("namespace"),
                row.get("path"),
                row.get("description"),
                row.get("visibility"),
                row.get("storage_type"),
                row.get("storage_provider"),
                row.get("source_path"),
                row.get("target_path"),
                row.get("readonly"),
                json.dumps(row.get("keywords") or [], ensure_ascii=False),
                row.get("created_by"),
                row.get("creation_date"),
                json.dumps(raw, ensure_ascii=False),
                self._now(),
            ],
        )

    def upsert_group_members(
        self,
        group_id: str,
        members: Iterable[dict[str, Any]],
    ) -> None:
        conn = self.connect()
        for m in members:
            user_id = m.get("user_id") or m.get("id")
            if not user_id:
                continue
            conn.execute(
                "INSERT INTO group_members (group_id, user_id, role) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT (group_id, user_id) DO UPDATE SET "
                "  role = excluded.role",
                [group_id, user_id, m.get("role")],
            )

    def upsert_project_members(
        self,
        project_id: str,
        members: Iterable[dict[str, Any]],
    ) -> None:
        conn = self.connect()
        for m in members:
            user_id = m.get("user_id") or m.get("id")
            if not user_id:
                continue
            conn.execute(
                "INSERT INTO project_members (project_id, user_id, role) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT (project_id, user_id) DO UPDATE SET "
                "  role = excluded.role",
                [project_id, user_id, m.get("role")],
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

    def list_group_ids(self) -> list[str]:
        cur = self.connect().execute("SELECT group_id FROM groups")
        return [str(r[0]) for r in cur.fetchall()]

    def list_project_ids(self) -> list[str]:
        cur = self.connect().execute("SELECT project_id FROM projects")
        return [str(r[0]) for r in cur.fetchall()]

    def fetch_entity(
        self,
        entity_type: str,
        entity_id: str,
    ) -> dict[str, Any] | None:
        if entity_type not in _ENTITY_PK:
            message = f"Unknown entity_type: {entity_type}"
            raise ValueError(message)
        table, pk = _ENTITY_PK[entity_type]
        cur = self.connect().execute(
            f"SELECT * FROM {table} WHERE {pk} = ?",
            [entity_id],
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
        table, pk = _ENTITY_PK[entity_type]
        sql = (
            f"SELECT t.* FROM {table} t "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM chunks c "
            f"  WHERE c.entity_type = ? AND c.entity_id = t.{pk}"
            ")"
        )
        params: list[Any] = [entity_type]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        cur = self.connect().execute(sql, params)
        cols = [d[0] for d in cur.description]
        # Materialize up front: see equivalent note in the Zenodo store.
        rows = cur.fetchall()
        for row in rows:
            yield dict(zip(cols, row, strict=False))

    @staticmethod
    def _now() -> str:
        return datetime.now(tz=timezone.utc).isoformat()
