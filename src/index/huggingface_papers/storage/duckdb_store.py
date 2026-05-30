"""DuckDB store for the huggingface_papers index."""

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
from src.index.huggingface_papers.paths import (
    get_huggingface_papers_paths,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from src.index.huggingface_papers.models import PaperRecord

LOGGER = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

ENTITY_TYPE = "papers"
ID_COLUMN = "arxiv_id"


class HuggingFacePapersStore:
    """Thin DuckDB wrapper for the `papers` schema. `bootstrap()` is idempotent."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: duckdb.DuckDBPyConnection | None = None

    @classmethod
    def open(cls, db_path: Path | None = None) -> HuggingFacePapersStore:
        if db_path is None:
            db_path = get_huggingface_papers_paths().duckdb_path
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

    # ---- Upserts ---------------------------------------------------------

    def upsert_paper(self, record: PaperRecord) -> None:
        sql = (
            "INSERT INTO papers "
            "(arxiv_id, title, summary, doi, authors, "
            " published_at, submitted_at, upvotes, num_comments, "
            " is_author_participating, ai_summary, ai_keywords, "
            " thumbnail, linked_models, linked_datasets, raw, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (arxiv_id) DO UPDATE SET "
            "  title = excluded.title, summary = excluded.summary, "
            "  doi = excluded.doi, authors = excluded.authors, "
            "  published_at = excluded.published_at, "
            "  submitted_at = excluded.submitted_at, "
            "  upvotes = excluded.upvotes, "
            "  num_comments = excluded.num_comments, "
            "  is_author_participating = excluded.is_author_participating, "
            "  ai_summary = excluded.ai_summary, "
            "  ai_keywords = excluded.ai_keywords, "
            "  thumbnail = excluded.thumbnail, "
            "  linked_models = excluded.linked_models, "
            "  linked_datasets = excluded.linked_datasets, "
            "  raw = excluded.raw, ingested_at = excluded.ingested_at"
        )
        self.connect().execute(
            sql,
            [
                record.arxiv_id,
                record.title,
                record.summary,
                record.doi,
                json.dumps(
                    [a.model_dump() for a in record.authors],
                    ensure_ascii=False,
                ),
                record.published_at,
                record.submitted_at,
                record.upvotes,
                record.num_comments,
                record.is_author_participating,
                record.ai_summary,
                json.dumps(record.ai_keywords, ensure_ascii=False),
                record.thumbnail,
                json.dumps(record.linked_models, ensure_ascii=False, default=str),
                json.dumps(record.linked_datasets, ensure_ascii=False, default=str),
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

    # ---- Reads -----------------------------------------------------------

    def count(self, table: str) -> int:
        return count_table(self.connect(), table)

    def fetch_paper(self, arxiv_id: str) -> dict[str, Any] | None:
        return fetch_one(
            self.connect(),
            table="papers",
            id_column=ID_COLUMN,
            id_value=arxiv_id,
        )

    def stream_rows_for_embedding(
        self,
        entity_type: str,
        *,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        if entity_type != ENTITY_TYPE:
            message = f"Unknown entity_type: {entity_type!r} (expected {ENTITY_TYPE!r})"
            raise ValueError(message)
        yield from stream_unembedded(
            self.connect(),
            table="papers",
            id_column=ID_COLUMN,
            entity_type=ENTITY_TYPE,
            limit=limit,
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(tz=timezone.utc).isoformat()
