"""DuckDB store for the huggingface_spaces index."""

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
from src.index.huggingface_spaces.paths import (
    get_huggingface_spaces_paths,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from src.index.huggingface_spaces.models import SpaceRecord

LOGGER = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

ENTITY_TYPE = "spaces"
ID_COLUMN = "repo_id"


class HuggingFaceSpacesStore:
    """Thin DuckDB wrapper for the HF spaces schema."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: duckdb.DuckDBPyConnection | None = None

    @classmethod
    def open(cls, db_path: Path | None = None) -> HuggingFaceSpacesStore:
        if db_path is None:
            db_path = get_huggingface_spaces_paths().duckdb_path
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

    def upsert_space(self, record: SpaceRecord) -> None:
        sql = (
            "INSERT INTO spaces "
            "(repo_id, author, sha, sdk, runtime_stage, hardware, license, "
            " likes, created_at, last_modified, tags, card_data, raw, "
            " ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (repo_id) DO UPDATE SET "
            "  author = excluded.author, sha = excluded.sha, "
            "  sdk = excluded.sdk, runtime_stage = excluded.runtime_stage, "
            "  hardware = excluded.hardware, license = excluded.license, "
            "  likes = excluded.likes, created_at = excluded.created_at, "
            "  last_modified = excluded.last_modified, tags = excluded.tags, "
            "  card_data = excluded.card_data, raw = excluded.raw, "
            "  ingested_at = excluded.ingested_at"
        )
        self.connect().execute(
            sql,
            [
                record.repo_id,
                record.author,
                record.sha,
                record.sdk,
                record.runtime_stage,
                record.hardware,
                record.license,
                record.likes,
                record.created_at,
                record.last_modified,
                json.dumps(record.tags, ensure_ascii=False),
                json.dumps(record.card_data, ensure_ascii=False, default=str),
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

    def fetch_space(self, repo_id: str) -> dict[str, Any] | None:
        return fetch_one(
            self.connect(),
            table="spaces",
            id_column=ID_COLUMN,
            id_value=repo_id,
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
            table="spaces",
            id_column=ID_COLUMN,
            entity_type=ENTITY_TYPE,
            limit=limit,
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(tz=timezone.utc).isoformat()
