"""Minimal DuckDB store for the zenodo_communities index."""

from __future__ import annotations

import contextlib
import json
import logging
from pathlib import Path
from typing import Any, Iterator, Optional

import duckdb

logger = logging.getLogger(__name__)


def _load_schema_sql() -> str:
    path = Path(__file__).parent / "schema.sql"
    return path.read_text(encoding="utf-8")


class ZenodoCommunitiesStore:
    """Tiny wrapper — `open()`, `bootstrap()`, `upsert()`, `count()`."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def open(cls, db_path: Optional[Path] = None) -> "ZenodoCommunitiesStore":
        from src.index.zenodo_communities.paths import duckdb_path  # noqa: PLC0415

        store = cls(db_path or duckdb_path())
        store.bootstrap()
        return store

    def _connect(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(str(self._db_path))

    @contextlib.contextmanager
    def read_only(self) -> Iterator[duckdb.DuckDBPyConnection]:
        con = duckdb.connect(str(self._db_path), read_only=True)
        try:
            yield con
        finally:
            con.close()

    def bootstrap(self) -> None:
        with self._connect() as con:
            for statement in _load_schema_sql().split(";"):
                stmt = statement.strip()
                if stmt:
                    con.execute(stmt + ";")

    def count(self) -> int:
        with self._connect() as con:
            return con.execute("SELECT COUNT(*) FROM communities").fetchone()[0]

    def upsert(self, row: dict[str, Any]) -> None:
        """Insert-or-replace one community row."""
        # JSON columns must be serialised; let DuckDB receive strings.
        for key in ("curator_names", "keywords", "raw"):
            value = row.get(key)
            if isinstance(value, (list, dict)):
                row[key] = json.dumps(value, ensure_ascii=False)
        cols = [
            "community_id", "source", "source_slug", "parent_org",
            "title", "description", "url", "visibility",
            "created_at", "updated_at",
            "curator_names", "member_count", "record_count",
            "keywords", "raw",
        ]
        placeholders = ", ".join(["?"] * len(cols))
        values = [row.get(c) for c in cols]
        with self._connect() as con:
            con.execute(
                f"INSERT OR REPLACE INTO communities ({', '.join(cols)}) "
                f"VALUES ({placeholders})",
                values,
            )

    def upsert_many(self, rows: list[dict[str, Any]]) -> int:
        ok = 0
        for row in rows:
            try:
                self.upsert(row)
                ok += 1
            except Exception:  # noqa: BLE001
                logger.exception(
                    "zenodo_communities upsert failed for %s",
                    row.get("community_id"),
                )
        return ok
