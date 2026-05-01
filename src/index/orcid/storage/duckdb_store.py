"""DuckDB lifecycle, schema bootstrap, and upsert helpers for ORCID."""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb

from src.index.orcid.paths import get_orcid_paths

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

LOGGER = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

VALID_AFFILIATION_TABLES = {"employments", "educations"}
VALID_EMBEDDING_ENTITIES = {"persons", "employments", "educations"}


def _load_schema_sql() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


class OrcidDuckDBStore:
    """Thin wrapper around DuckDB tuned for the ORCID schema.

    Construct with `OrcidDuckDBStore.open()` for the scope-resolved repo
    path. Re-running `bootstrap()` is idempotent.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: duckdb.DuckDBPyConnection | None = None

    @classmethod
    def open(
        cls,
        db_path: Path | None = None,
        *,
        scope: str | None = None,
    ) -> OrcidDuckDBStore:
        if db_path is None:
            db_path = get_orcid_paths(scope).duckdb_path
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

    def upsert_seed(
        self,
        *,
        orcid_id: str,
        discovered_via: str,
        hint: str | None = None,
    ) -> None:
        self.connect().execute(
            "INSERT INTO seeds (orcid_id, discovered_via, hint) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT (orcid_id) DO UPDATE SET "
            "  discovered_via = CASE "
            "    WHEN seeds.discovered_via = excluded.discovered_via THEN seeds.discovered_via "
            "    ELSE 'both' "
            "  END, "
            "  hint = COALESCE(excluded.hint, seeds.hint)",
            [orcid_id, discovered_via, hint],
        )

    def upsert_person(self, row: dict[str, Any], raw: dict[str, Any]) -> None:
        cols = (
            "orcid_id",
            "given_name",
            "family_name",
            "display_name",
            "biography",
            "in_scope",
            "scope_reason",
            "discovered_via",
        )
        all_cols = (*cols, "raw", "ingested_at")
        placeholders = ", ".join(["?"] * len(all_cols))
        col_list = ", ".join(all_cols)
        update_cols = ", ".join(
            f"{c} = excluded.{c}" for c in (*cols[1:], "raw", "ingested_at")
        )
        sql = (  # noqa: S608 - column names sourced from the literal `cols` tuple above
            f"INSERT INTO persons ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT (orcid_id) DO UPDATE SET {update_cols}"
        )
        values: list[Any] = [row.get(c) for c in cols]
        values.append(json.dumps(raw, ensure_ascii=False))
        values.append(self._now())
        self.connect().execute(sql, values)

    def replace_affiliations(
        self,
        table: str,
        orcid_id: str,
        rows: Iterable[dict[str, Any]],
    ) -> int:
        if table not in VALID_AFFILIATION_TABLES:
            message = f"Unknown affiliation table: {table}"
            raise ValueError(message)
        conn = self.connect()
        # Replace-all semantics keeps the table consistent when ORCID
        # entries are removed/renumbered upstream.
        conn.execute(
            f"DELETE FROM {table} WHERE orcid_id = ?",  # noqa: S608 - guarded
            [orcid_id],
        )
        cols = (
            "orcid_id",
            "seq",
            "organization",
            "org_ror",
            "department",
            "role",
            "start_date",
            "end_date",
        )
        col_list = ", ".join(cols)
        placeholders = ", ".join(["?"] * len(cols))
        sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"  # noqa: S608
        count = 0
        for row in rows:
            conn.execute(sql, [row.get(c) for c in cols])
            count += 1
        return count

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
        result = self.connect().execute(
            f"SELECT count(*) FROM {table}",  # noqa: S608 - callers pass internal table names
        ).fetchone()
        return int(result[0]) if result else 0

    def fetch_person(self, orcid_id: str) -> dict[str, Any] | None:
        cur = self.connect().execute(
            "SELECT * FROM persons WHERE orcid_id = ?",
            [orcid_id],
        )
        row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row, strict=False))

    def stream_seeds(self, *, only_unfetched: bool = True) -> Iterator[dict[str, Any]]:
        """Yield seed rows. With `only_unfetched`, skip ORCIDs already in `persons`.

        Materializes the result upfront because callers (e.g. the persons
        ingester) write into `persons` between iterations, which would
        invalidate a streaming cursor that joins `persons` in its query.
        """
        sql = (
            "SELECT s.orcid_id, s.discovered_via, s.hint FROM seeds s "
            "LEFT JOIN persons p ON p.orcid_id = s.orcid_id "
        )
        if only_unfetched:
            sql += "WHERE p.orcid_id IS NULL "
        sql += "ORDER BY s.discovered_at"
        cur = self.connect().execute(sql)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        for row in rows:
            yield dict(zip(cols, row, strict=False))

    def stream_rows_for_embedding(
        self,
        entity_type: str,
        *,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield rows that need embedding (no chunks yet, in-scope only)."""
        if entity_type not in VALID_EMBEDDING_ENTITIES:
            message = f"Unknown entity_type: {entity_type}"
            raise ValueError(message)
        if entity_type == "persons":
            join_id = "t.orcid_id"
            sql = (
                "SELECT t.* FROM persons t "
                "WHERE t.in_scope = TRUE AND NOT EXISTS ("
                "  SELECT 1 FROM chunks c "
                "  WHERE c.entity_type = ? AND c.entity_id = t.orcid_id"
                ") "
            )
            params: list[Any] = [entity_type]
        else:
            # Affiliations: only embed if the parent person is in scope.
            join_id = "t.orcid_id || '#' || CAST(t.seq AS VARCHAR)"
            sql = (
                f"SELECT t.*, {join_id} AS entity_id "  # noqa: S608 - table guarded
                f"FROM {entity_type} t "
                "JOIN persons p ON p.orcid_id = t.orcid_id "
                "WHERE p.in_scope = TRUE AND NOT EXISTS ("
                "  SELECT 1 FROM chunks c "
                f"  WHERE c.entity_type = ? AND c.entity_id = {join_id}"
                ") "
            )
            params = [entity_type]
        if limit is not None:
            sql += "LIMIT ?"
            params.append(limit)
        cur = self.connect().execute(sql, params)
        cols = [d[0] for d in cur.description]
        # Materialize upfront — embed loop writes to `chunks` between rows,
        # which would invalidate a streaming cursor that joins `chunks` via
        # NOT EXISTS in its query.
        rows = cur.fetchall()
        for row in rows:
            yield dict(zip(cols, row, strict=False))

    def list_employments(self, orcid_id: str) -> list[dict[str, Any]]:
        cur = self.connect().execute(
            "SELECT * FROM employments WHERE orcid_id = ? ORDER BY seq",
            [orcid_id],
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]

    @staticmethod
    def _now() -> str:
        return datetime.now(tz=timezone.utc).isoformat()
