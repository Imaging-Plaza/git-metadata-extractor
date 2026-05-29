"""DuckDB lifecycle, schema bootstrap, and upsert helpers."""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb

from src.index.openalex.paths import get_openalex_paths

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

LOGGER = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _load_schema_sql() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


class OpenAlexStore:
    """Thin wrapper around DuckDB tuned for the OpenAlex schema.

    Construct with `OpenAlexStore.open()` for the default repo path. Re-running
    `bootstrap()` is idempotent.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: duckdb.DuckDBPyConnection | None = None

    @classmethod
    def open(cls, db_path: Path | None = None) -> OpenAlexStore:
        if db_path is None:
            db_path = get_openalex_paths().duckdb_path
        store = cls(db_path)
        store.bootstrap()
        return store

    def connect(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = duckdb.connect(str(self.db_path))
        return self._conn

    def bootstrap(self) -> None:
        """Apply the canonical schema. Safe to call repeatedly."""
        conn = self.connect()
        conn.execute(_load_schema_sql())

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @contextmanager
    def read_only(self) -> Iterator[duckdb.DuckDBPyConnection]:
        """Open a fresh read-only connection (separate from the writer)."""
        ro = duckdb.connect(str(self.db_path), read_only=True)
        try:
            yield ro
        finally:
            ro.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Wrap a batch of writes in a single BEGIN/COMMIT for throughput.

        DuckDB auto-commits each `execute()` by default, which makes per-row
        inserts ~5–10× slower than batched ones. Wrapping ingest pages in
        this context manager folds them into a single commit.
        """
        conn = self.connect()
        conn.execute("BEGIN TRANSACTION")
        try:
            yield
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")

    # ---- Upserts ---------------------------------------------------------

    def upsert_work(self, row: dict[str, Any], raw: dict[str, Any]) -> None:
        self._upsert(
            table="works",
            cols=(
                "openalex_id",
                "doi",
                "title",
                "abstract",
                "publication_year",
                "primary_topic_id",
                "primary_source_id",
            ),
            row=row,
            raw=raw,
        )

    def upsert_author(self, row: dict[str, Any], raw: dict[str, Any]) -> None:
        self._upsert(
            table="authors",
            cols=(
                "openalex_id",
                "display_name",
                "orcid",
                "last_known_institution_id",
            ),
            row=row,
            raw=raw,
        )

    def upsert_institution(self, row: dict[str, Any], raw: dict[str, Any]) -> None:
        self._upsert(
            table="institutions",
            cols=("openalex_id", "ror", "display_name", "country_code"),
            row=row,
            raw=raw,
        )

    def upsert_source(self, row: dict[str, Any], raw: dict[str, Any]) -> None:
        self._upsert(
            table="sources",
            cols=("openalex_id", "issn_l", "display_name", "type"),
            row=row,
            raw=raw,
        )

    def upsert_topic(self, row: dict[str, Any], raw: dict[str, Any]) -> None:
        self._upsert(
            table="topics",
            cols=("openalex_id", "display_name", "domain_id", "field_id"),
            row=row,
            raw=raw,
        )

    def upsert_concept(self, row: dict[str, Any], raw: dict[str, Any]) -> None:
        self._upsert(
            table="concepts",
            cols=("openalex_id", "display_name", "level"),
            row=row,
            raw=raw,
        )

    def _upsert(
        self,
        *,
        table: str,
        cols: tuple[str, ...],
        row: dict[str, Any],
        raw: dict[str, Any],
    ) -> None:
        all_cols = (*cols, "raw", "ingested_at")
        placeholders = ", ".join(["?"] * len(all_cols))
        col_list = ", ".join(all_cols)
        update_cols = ", ".join(
            f"{c} = excluded.{c}" for c in (*cols[1:], "raw", "ingested_at")
        )
        sql = (
            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT ({cols[0]}) DO UPDATE SET {update_cols}"
        )
        values = [row.get(c) for c in cols]
        values.append(json.dumps(raw, ensure_ascii=False))
        values.append(self._now())
        self.connect().execute(sql, values)

    def upsert_work_authors(
        self,
        work_id: str,
        author_positions: Iterable[tuple[str, int]],
    ) -> None:
        conn = self.connect()
        for author_id, position in author_positions:
            conn.execute(
                "INSERT INTO work_authors (work_id, author_id, position) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT (work_id, author_id) DO UPDATE SET position = "
                "LEAST(work_authors.position, excluded.position)",
                [work_id, author_id, position],
            )

    def upsert_work_institutions(
        self,
        work_id: str,
        institution_ids: Iterable[str],
    ) -> None:
        conn = self.connect()
        for inst_id in institution_ids:
            conn.execute(
                "INSERT INTO work_institutions (work_id, institution_id) "
                "VALUES (?, ?) ON CONFLICT DO NOTHING",
                [work_id, inst_id],
            )

    def upsert_github_url(
        self,
        *,
        work_id: str,
        url: str,
        normalized_url: str,
        owner: str | None,
        repo: str | None,
        source: str,
    ) -> None:
        self.connect().execute(
            "INSERT INTO work_github_urls "
            "(work_id, url, normalized_url, owner, repo, source) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (work_id, normalized_url) DO UPDATE SET "
            "url = excluded.url, owner = excluded.owner, "
            "repo = excluded.repo, source = excluded.source",
            [work_id, url, normalized_url, owner, repo, source],
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

    def fetch_work(self, openalex_id: str) -> dict[str, Any] | None:
        return self._fetch_one("works", openalex_id)

    def _fetch_one(self, table: str, openalex_id: str) -> dict[str, Any] | None:
        cur = self.connect().execute(
            f"SELECT * FROM {table} WHERE openalex_id = ?",
            [openalex_id],
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
        """Yield rows that need embedding (no chunks yet OR `limit` reached).

        Materializes the full result set upfront — DuckDB has one cursor per
        connection, so leaving a live SELECT cursor while the embed pipeline
        issues `upsert_chunk` calls on the same connection silently truncates
        the stream.
        """
        if entity_type not in {
            "works",
            "authors",
            "institutions",
            "sources",
            "topics",
            "concepts",
        }:
            message = f"Unknown entity_type: {entity_type}"
            raise ValueError(message)
        sql = (
            f"SELECT t.* FROM {entity_type} t "  # noqa: S608 - table name guarded above
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM chunks c "
            "  WHERE c.entity_type = ? AND c.entity_id = t.openalex_id"
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
        from datetime import datetime, timezone

        return datetime.now(tz=timezone.utc).isoformat()
