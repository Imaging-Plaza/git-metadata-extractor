"""DuckDB lifecycle, schema bootstrap, and upserts for the OAM-CH index.

Holds four tables — ``journals``, ``publications``, ``publishers``,
``organisations`` — mirroring the upstream OAM Mongo collections. Each row
preserves the original ``_id`` and a full ``raw`` JSON copy so we never
lose upstream fields we didn't bother to denormalise.
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb

from src.index.oamonitor.paths import get_oamonitor_paths

if TYPE_CHECKING:
    from collections.abc import Iterator

LOGGER = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

ENTITY_TABLES: tuple[str, ...] = (
    "journals",
    "publications",
    "publishers",
    "organisations",
)


def _load_schema_sql() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


class OamonitorStore:
    """Thin DuckDB wrapper. ``bootstrap()`` is idempotent."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: duckdb.DuckDBPyConnection | None = None

    @classmethod
    def open(cls, db_path: Path | None = None) -> OamonitorStore:
        if db_path is None:
            db_path = get_oamonitor_paths().duckdb_path
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

    def upsert_journal(self, row: dict[str, Any]) -> None:
        sql = (
            "INSERT INTO journals "
            "(_id, title, oa_color, issns, updated, embedding_text, raw) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (_id) DO UPDATE SET "
            "  title = excluded.title, oa_color = excluded.oa_color, "
            "  issns = excluded.issns, updated = excluded.updated, "
            "  embedding_text = excluded.embedding_text, "
            "  raw = excluded.raw, fetched_at = now()"
        )
        self.connect().execute(
            sql,
            [
                row["_id"],
                row.get("title"),
                row.get("oa_color"),
                list(row.get("issns") or []),
                row.get("updated"),
                row.get("embedding_text"),
                json.dumps(row.get("raw") or {}, ensure_ascii=False, default=str),
            ],
        )

    def upsert_publication(self, row: dict[str, Any]) -> None:
        sql = (
            "INSERT INTO publications "
            "(_id, doi, url, oa_color, license, published_year, "
            " publisher_id, publisher_name, source_id, source_title, "
            " organisation_ids, updated, embedding_text, raw) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (_id) DO UPDATE SET "
            "  doi = excluded.doi, url = excluded.url, "
            "  oa_color = excluded.oa_color, license = excluded.license, "
            "  published_year = excluded.published_year, "
            "  publisher_id = excluded.publisher_id, "
            "  publisher_name = excluded.publisher_name, "
            "  source_id = excluded.source_id, "
            "  source_title = excluded.source_title, "
            "  organisation_ids = excluded.organisation_ids, "
            "  updated = excluded.updated, "
            "  embedding_text = excluded.embedding_text, "
            "  raw = excluded.raw, fetched_at = now()"
        )
        self.connect().execute(
            sql,
            [
                row["_id"],
                row.get("doi"),
                row.get("url"),
                row.get("oa_color"),
                row.get("license"),
                row.get("published_year"),
                row.get("publisher_id"),
                row.get("publisher_name"),
                row.get("source_id"),
                row.get("source_title"),
                list(row.get("organisation_ids") or []),
                row.get("updated"),
                row.get("embedding_text"),
                json.dumps(row.get("raw") or {}, ensure_ascii=False, default=str),
            ],
        )

    def upsert_publisher(self, row: dict[str, Any]) -> None:
        sql = (
            "INSERT INTO publishers "
            "(_id, name, oa_color, updated, embedding_text, raw) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (_id) DO UPDATE SET "
            "  name = excluded.name, oa_color = excluded.oa_color, "
            "  updated = excluded.updated, "
            "  embedding_text = excluded.embedding_text, "
            "  raw = excluded.raw, fetched_at = now()"
        )
        self.connect().execute(
            sql,
            [
                row["_id"],
                row.get("name"),
                row.get("oa_color"),
                row.get("updated"),
                row.get("embedding_text"),
                json.dumps(row.get("raw") or {}, ensure_ascii=False, default=str),
            ],
        )

    def upsert_organisation(self, row: dict[str, Any]) -> None:
        sql = (
            "INSERT INTO organisations "
            "(_id, name, type, grid_id, country_code, acronyms, aliases, "
            " updated, embedding_text, raw) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (_id) DO UPDATE SET "
            "  name = excluded.name, type = excluded.type, "
            "  grid_id = excluded.grid_id, country_code = excluded.country_code, "
            "  acronyms = excluded.acronyms, aliases = excluded.aliases, "
            "  updated = excluded.updated, "
            "  embedding_text = excluded.embedding_text, "
            "  raw = excluded.raw, fetched_at = now()"
        )
        self.connect().execute(
            sql,
            [
                row["_id"],
                row.get("name"),
                row.get("type"),
                row.get("grid_id"),
                row.get("country_code"),
                list(row.get("acronyms") or []),
                list(row.get("aliases") or []),
                row.get("updated"),
                row.get("embedding_text"),
                json.dumps(row.get("raw") or {}, ensure_ascii=False, default=str),
            ],
        )

    # ---- Reads -----------------------------------------------------------

    def count(self, table: str) -> int:
        if table not in ENTITY_TABLES:
            message = f"Unsupported table: {table}"
            raise ValueError(message)
        row = self.connect().execute(
            f"SELECT COUNT(*) FROM {table}",  # noqa: S608 — whitelisted table
        ).fetchone()
        return int(row[0]) if row else 0

    def iter_rows_for_embedding(
        self,
        table: str,
        *,
        min_length: int = 1,
        only_unembedded: bool = True,
    ) -> Iterator[dict[str, Any]]:
        """Yield rows with non-empty ``embedding_text``.

        ``only_unembedded=True`` (default) skips rows that have a populated
        ``embedded_at`` whose value is at or after the row's ``updated``
        timestamp — i.e. the Qdrant point is in sync. Set to ``False`` to
        re-embed everything.
        """
        if table not in ENTITY_TABLES:
            message = f"Unsupported table: {table}"
            raise ValueError(message)
        clauses = [
            "embedding_text IS NOT NULL",
            "LENGTH(embedding_text) >= ?",
        ]
        if only_unembedded:
            clauses.append("(embedded_at IS NULL OR embedded_at < updated)")
        where = " AND ".join(clauses)
        cursor = self.connect().execute(
            f"SELECT _id, embedding_text FROM {table} "  # noqa: S608
            f"WHERE {where} "
            "ORDER BY _id",
            [int(min_length)],
        )
        for row in cursor.fetchall():
            yield {"_id": row[0], "embedding_text": row[1]}

    def mark_embedded(self, table: str, entity_ids: list[str]) -> None:
        """Stamp ``embedded_at = now()`` on each ``_id`` after a Qdrant push."""
        if table not in ENTITY_TABLES:
            message = f"Unsupported table: {table}"
            raise ValueError(message)
        if not entity_ids:
            return
        placeholders = ",".join(["?"] * len(entity_ids))
        self.connect().execute(
            f"UPDATE {table} SET embedded_at = now() "  # noqa: S608
            f"WHERE _id IN ({placeholders})",
            entity_ids,
        )

    def count_unembedded(self, table: str) -> int:
        if table not in ENTITY_TABLES:
            message = f"Unsupported table: {table}"
            raise ValueError(message)
        row = self.connect().execute(
            f"SELECT COUNT(*) FROM {table} "  # noqa: S608
            "WHERE embedding_text IS NOT NULL "
            "  AND LENGTH(embedding_text) >= 1 "
            "  AND (embedded_at IS NULL OR embedded_at < updated)",
        ).fetchone()
        return int(row[0]) if row else 0


__all__ = ["ENTITY_TABLES", "OamonitorStore"]
