"""DuckDB lifecycle, schema bootstrap, and upsert helpers for the
infoscience index. Mirrors `src/index/openalex/storage/duckdb_store.py`.

Source of truth for ingest is the on-disk `raw/{items,persons,organizations}/`
JSON tree produced by the discover / fetch-related stages and the
`scripts/dump_link_articles.py` link sweep. Re-ingesting from those JSONs
is idempotent — every upsert keys on the DSpace UUID.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Iterator

import duckdb

from src.index.infoscience.paths import duckdb_path

if TYPE_CHECKING:
    pass

LOGGER = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _load_schema_sql() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


class DuckDBStore:
    """Thin wrapper around DuckDB tuned for the infoscience schema.

    Construct with `DuckDBStore.open()` for the canonical path. Re-running
    `bootstrap()` is idempotent; `transaction()` batches writes.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: duckdb.DuckDBPyConnection | None = None

    @classmethod
    def open(cls, db_path: Path | None = None) -> "DuckDBStore":
        if db_path is None:
            db_path = duckdb_path()
        store = cls(db_path)
        store.bootstrap()
        return store

    def connect(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = duckdb.connect(str(self.db_path))
        return self._conn

    def bootstrap(self) -> None:
        conn = self.connect()
        conn.execute(_load_schema_sql())

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

    @contextmanager
    def transaction(self) -> Iterator[None]:
        conn = self.connect()
        conn.execute("BEGIN TRANSACTION")
        try:
            yield
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")

    def count(self, table: str) -> int:
        conn = self.connect()
        return int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])

    @staticmethod
    def _now() -> _dt.datetime:
        return _dt.datetime.now(tz=_dt.timezone.utc).replace(tzinfo=None)

    # ---- Upserts ---------------------------------------------------------

    def upsert_article(self, row: dict[str, Any], raw: dict[str, Any]) -> None:
        self._upsert(
            table="articles",
            cols=(
                "article_uuid",
                "title",
                "abstract",
                "doi",
                "publication_year",
                "publication_type",
                "journal",
                "language",
                "infoscience_url",
            ),
            row=row,
            raw=raw,
        )

    def upsert_person(self, row: dict[str, Any], raw: dict[str, Any]) -> None:
        self._upsert(
            table="persons",
            cols=(
                "person_uuid",
                "display_name",
                "given_name",
                "family_name",
                "orcid",
                "sciper_id",
                "primary_affiliation",
                "primary_affiliation_uuid",
            ),
            row=row,
            raw=raw,
        )

    def upsert_organization(self, row: dict[str, Any], raw: dict[str, Any]) -> None:
        self._upsert(
            table="organizations",
            cols=(
                "org_uuid",
                "name",
                "acronym",
                "parent_org_uuid",
                "sciper_unit_id",
                "ror_id",
            ),
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
        values: list[Any] = [row.get(c) for c in cols]
        values.append(json.dumps(raw, ensure_ascii=False))
        values.append(self._now())
        self.connect().execute(sql, values)

    def upsert_article_persons(
        self,
        article_uuid: str,
        person_positions: Iterable[tuple[str, int | None]],
    ) -> None:
        conn = self.connect()
        for person_uuid, position in person_positions:
            conn.execute(
                "INSERT INTO article_persons (article_uuid, person_uuid, position) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT (article_uuid, person_uuid) DO UPDATE SET position = "
                "COALESCE(LEAST(article_persons.position, excluded.position), "
                "         excluded.position, article_persons.position)",
                [article_uuid, person_uuid, position],
            )

    def upsert_article_orgs(
        self,
        article_uuid: str,
        org_field_pairs: Iterable[tuple[str, str]],
    ) -> None:
        conn = self.connect()
        for org_uuid, field in org_field_pairs:
            conn.execute(
                "INSERT INTO article_orgs (article_uuid, org_uuid, field) "
                "VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                [article_uuid, org_uuid, field],
            )

    def upsert_article_links(
        self,
        article_uuid: str,
        rows: Iterable[tuple[str, str, str]],
    ) -> None:
        """rows: iterable of (host_label, url, source)."""
        conn = self.connect()
        for host_label, url, source in rows:
            conn.execute(
                "INSERT INTO article_links (article_uuid, host_label, url, source) "
                "VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
                [article_uuid, host_label, url, source],
            )
