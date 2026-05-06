"""DuckDB lifecycle, schema bootstrap, and upserts for the disciplines index.

Mirrors the slim shape of `src/index/zenodo/storage/duckdb_store.py`.
The store holds two tables: ``categories`` (one row per ontology node)
and ``category_concepts`` (top-N anchor Wikipedia concepts per category).
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb

from src.index.epfl_graph.paths import get_epfl_graph_paths

if TYPE_CHECKING:
    from collections.abc import Iterator

LOGGER = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _load_schema_sql() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


class EpflGraphStore:
    """Thin DuckDB wrapper. ``bootstrap()`` is idempotent."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: duckdb.DuckDBPyConnection | None = None

    @classmethod
    def open(cls, db_path: Path | None = None) -> EpflGraphStore:
        if db_path is None:
            db_path = get_epfl_graph_paths().duckdb_path
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

    def upsert_category(
        self,
        row: dict[str, Any],
        raw: dict[str, Any],
        concepts: list[dict[str, Any]] | None = None,
    ) -> None:
        sql = (
            "INSERT INTO categories "
            "(category_id, name, depth, parent_id, wikipedia_page_id, "
            " wikipedia_url, graphsearch_url, n_concepts, n_children, "
            " embedding_text, raw) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (category_id) DO UPDATE SET "
            "  name = excluded.name, depth = excluded.depth, "
            "  parent_id = excluded.parent_id, "
            "  wikipedia_page_id = excluded.wikipedia_page_id, "
            "  wikipedia_url = excluded.wikipedia_url, "
            "  graphsearch_url = excluded.graphsearch_url, "
            "  n_concepts = excluded.n_concepts, "
            "  n_children = excluded.n_children, "
            "  embedding_text = excluded.embedding_text, "
            "  raw = excluded.raw, fetched_at = now()"
        )
        self.connect().execute(
            sql,
            [
                row["category_id"],
                row.get("name"),
                row.get("depth"),
                row.get("parent_id"),
                row.get("wikipedia_page_id"),
                row.get("wikipedia_url"),
                row.get("graphsearch_url"),
                int(row.get("n_concepts") or 0),
                int(row.get("n_children") or 0),
                row.get("embedding_text"),
                json.dumps(raw, ensure_ascii=False),
            ],
        )
        if concepts:
            self.upsert_concepts(row["category_id"], concepts)

    def upsert_concepts(
        self, category_id: str, concepts: list[dict[str, Any]],
    ) -> None:
        sql = (
            "INSERT INTO category_concepts "
            "(category_id, concept_id, concept_name, rank) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT (category_id, concept_id) DO UPDATE SET "
            "  concept_name = excluded.concept_name, rank = excluded.rank"
        )
        rows = []
        for rank, concept in enumerate(concepts):
            cid = concept.get("id") or concept.get("concept_id")
            if not cid:
                continue
            rows.append(
                (
                    category_id,
                    str(cid),
                    concept.get("name") or concept.get("concept_name"),
                    rank,
                ),
            )
        if rows:
            self.connect().executemany(sql, rows)

    # ---- Reads -----------------------------------------------------------

    def fetch_category(self, category_id: str) -> dict[str, Any] | None:
        row = self.connect().execute(
            "SELECT category_id, name, depth, parent_id, "
            "       wikipedia_page_id, wikipedia_url, graphsearch_url, "
            "       n_concepts, n_children, embedding_text "
            "FROM categories WHERE category_id = ?",
            [category_id],
        ).fetchone()
        if not row:
            return None
        return {
            "category_id": row[0],
            "name": row[1],
            "depth": row[2],
            "parent_id": row[3],
            "wikipedia_page_id": row[4],
            "wikipedia_url": row[5],
            "graphsearch_url": row[6],
            "n_concepts": row[7],
            "n_children": row[8],
            "embedding_text": row[9],
        }

    # ---- Wikipedia enrichment -------------------------------------------

    def iter_categories_missing_extract(self) -> Iterator[dict[str, Any]]:
        cursor = self.connect().execute(
            "SELECT category_id, wikipedia_page_id, name "
            "FROM categories "
            "WHERE wikipedia_page_id IS NOT NULL "
            "  AND (wikipedia_extract IS NULL OR wikipedia_extract = '') "
            "ORDER BY category_id",
        )
        for row in cursor.fetchall():
            yield {
                "category_id": row[0],
                "wikipedia_page_id": row[1],
                "name": row[2],
            }

    def update_wikipedia_extract(
        self, category_id: str, extract: str | None,
    ) -> None:
        self.connect().execute(
            "UPDATE categories SET wikipedia_extract = ? WHERE category_id = ?",
            [extract, category_id],
        )

    def update_embedding_text(
        self, category_id: str, embedding_text: str | None,
    ) -> None:
        self.connect().execute(
            "UPDATE categories SET embedding_text = ? WHERE category_id = ?",
            [embedding_text, category_id],
        )

    def iter_categories_for_extract_refresh(self) -> Iterator[dict[str, Any]]:
        cursor = self.connect().execute(
            "SELECT c.category_id, c.name, c.wikipedia_extract "
            "FROM categories c ORDER BY c.category_id",
        )
        for row in cursor.fetchall():
            yield {
                "category_id": row[0],
                "name": row[1],
                "wikipedia_extract": row[2],
            }

    def fetch_anchor_concept_names(self, category_id: str, limit: int) -> list[str]:
        cursor = self.connect().execute(
            "SELECT concept_name FROM category_concepts "
            "WHERE category_id = ? AND concept_name IS NOT NULL "
            "ORDER BY rank ASC LIMIT ?",
            [category_id, int(limit)],
        )
        return [row[0] for row in cursor.fetchall() if row[0]]

    def iter_categories_for_embedding(
        self, *, min_depth: int = 3,
    ) -> Iterator[dict[str, Any]]:
        cursor = self.connect().execute(
            "SELECT category_id, name, depth, parent_id, "
            "       wikipedia_page_id, wikipedia_url, graphsearch_url, "
            "       n_concepts, n_children, embedding_text "
            "FROM categories "
            "WHERE depth >= ? AND embedding_text IS NOT NULL "
            "ORDER BY category_id",
            [int(min_depth)],
        )
        for row in cursor.fetchall():
            yield {
                "category_id": row[0],
                "name": row[1],
                "depth": row[2],
                "parent_id": row[3],
                "wikipedia_page_id": row[4],
                "wikipedia_url": row[5],
                "graphsearch_url": row[6],
                "n_concepts": row[7],
                "n_children": row[8],
                "embedding_text": row[9],
            }

    def count_categories(self) -> int:
        row = self.connect().execute(
            "SELECT COUNT(*) FROM categories",
        ).fetchone()
        return int(row[0]) if row else 0

    def count_concepts(self) -> int:
        row = self.connect().execute(
            "SELECT COUNT(*) FROM category_concepts",
        ).fetchone()
        return int(row[0]) if row else 0
