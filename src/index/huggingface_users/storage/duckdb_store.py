"""DuckDB store for the huggingface_users index."""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb

from src.index._github_accounts_base.storage_base import (
    bootstrap_schema,
    count_table,
    fetch_one,
    stream_unembedded,
    upsert_chunk,
)
from src.index.huggingface_users.paths import (
    get_huggingface_users_paths,
)

from src.v2.canonicalization.huggingface import huggingface_iri

if TYPE_CHECKING:
    from collections.abc import Iterator

    from src.index.huggingface_users.models import HFUserRecord

LOGGER = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

ENTITY_TYPE = "users"
ID_COLUMN = "slug"


class HuggingFaceUsersStore:
    """Thin DuckDB wrapper for the HF users schema."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: duckdb.DuckDBPyConnection | None = None

    @classmethod
    def open(cls, db_path: Path | None = None) -> HuggingFaceUsersStore:
        if db_path is None:
            db_path = get_huggingface_users_paths().duckdb_path
        store = cls(db_path)
        store.bootstrap()
        return store

    def connect(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = duckdb.connect(str(self.db_path))
        return self._conn

    def bootstrap(self) -> None:
        bootstrap_schema(self.connect(), SCHEMA_PATH)

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

    def upsert_user(self, record: HFUserRecord) -> None:
        sql = (
            "INSERT INTO users "
            "(slug, fullname, details, avatar_url, num_models, num_datasets, "
            " num_spaces, num_followers, raw, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (slug) DO UPDATE SET "
            "  fullname = excluded.fullname, details = excluded.details, "
            "  avatar_url = excluded.avatar_url, "
            "  num_models = excluded.num_models, "
            "  num_datasets = excluded.num_datasets, "
            "  num_spaces = excluded.num_spaces, "
            "  num_followers = excluded.num_followers, "
            "  raw = excluded.raw, ingested_at = excluded.ingested_at"
        )
        self.connect().execute(
            sql,
            [
                huggingface_iri(record.slug, "user") or record.slug,
                record.fullname,
                record.details,
                record.avatar_url,
                record.num_models,
                record.num_datasets,
                record.num_spaces,
                record.num_followers,
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
        upsert_chunk(
            self.connect(),
            chunk_id=chunk_id,
            entity_type=entity_type,
            entity_id=entity_id,
            chunk_index=chunk_index,
            text=text,
            token_count=token_count,
            vector_id=vector_id,
        )

    def count(self, table: str) -> int:
        return count_table(self.connect(), table)

    def fetch_user(self, slug: str) -> dict[str, Any] | None:
        return fetch_one(
            self.connect(),
            table="users",
            id_column=ID_COLUMN,
            id_value=huggingface_iri(slug, "user") or slug,
        )

    def stream_rows_for_embedding(
        self,
        entity_type: str,
        *,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        if entity_type != ENTITY_TYPE:
            message = f"Unknown entity_type: {entity_type!r}"
            raise ValueError(message)
        yield from stream_unembedded(
            self.connect(),
            table="users",
            id_column=ID_COLUMN,
            entity_type=ENTITY_TYPE,
            limit=limit,
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(tz=timezone.utc).isoformat()
