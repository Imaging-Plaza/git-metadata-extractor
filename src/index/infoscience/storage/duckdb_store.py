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
from src.v2.canonicalization.infoscience import (
    infoscience_article_iri,
    infoscience_iri_sql,
    infoscience_org_iri,
    infoscience_person_iri,
)

if TYPE_CHECKING:
    pass

LOGGER = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _load_schema_sql() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


class InfoscienceStore:
    """Thin wrapper around DuckDB tuned for the infoscience schema.

    Construct with `InfoscienceStore.open()` for the canonical path. Re-running
    `bootstrap()` is idempotent; `transaction()` batches writes.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: duckdb.DuckDBPyConnection | None = None

    @classmethod
    def open(cls, db_path: Path | None = None) -> "InfoscienceStore":
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
        # Promote `articles.doi` to canonical `https://doi.org/<bare>`.
        from src.index._shared.doi import (  # noqa: PLC0415
            migrate_doi_column_to_url,
        )

        migrate_doi_column_to_url(conn, table="articles", column="doi")
        self._migrate_ids_to_url(conn)

    @staticmethod
    def _migrate_ids_to_url(conn: duckdb.DuckDBPyConnection) -> None:
        """v3.0.0: promote bare DSpace UUID ids (and the junction FKs that
        reference them) to canonical Infoscience entity URLs. Idempotent —
        the guarded CASE only rewrites bare UUID4s, so already-canonical
        rows and re-runs are no-ops. Existing on-disk DBs converge on the
        next ``bootstrap()``.
        """

        def col(name: str, kind: str) -> str:
            return infoscience_iri_sql(name, kind)

        updates = (
            f"UPDATE articles SET article_uuid = {col('article_uuid', 'publication')}, "
            f"infoscience_url = {col('infoscience_url', 'publication')}",
            f"UPDATE persons SET person_uuid = {col('person_uuid', 'person')}, "
            f"primary_affiliation_uuid = {col('primary_affiliation_uuid', 'orgunit')}",
            f"UPDATE organizations SET org_uuid = {col('org_uuid', 'orgunit')}, "
            f"parent_org_uuid = {col('parent_org_uuid', 'orgunit')}, "
            f"infoscience_url = {col('infoscience_url', 'orgunit')}",
            f"UPDATE article_persons SET article_uuid = {col('article_uuid', 'publication')}, "
            f"person_uuid = {col('person_uuid', 'person')}",
            f"UPDATE article_orgs SET article_uuid = {col('article_uuid', 'publication')}, "
            f"org_uuid = {col('org_uuid', 'orgunit')}",
            f"UPDATE article_links SET article_uuid = {col('article_uuid', 'publication')}",
        )
        for stmt in updates:
            conn.execute(stmt)

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
        from src.index._shared.doi import doi_iri  # noqa: PLC0415

        if row.get("doi"):
            row = {**row, "doi": doi_iri(row["doi"])}
        # v3.0.0: the id is the canonical Infoscience publication URL.
        uid = row.get("article_uuid")
        row = {**row, "article_uuid": infoscience_article_iri(uid) or uid}
        if row.get("infoscience_url") is None and row.get("article_uuid"):
            row = {**row, "infoscience_url": row["article_uuid"]}
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
        # v3.0.0: person_uuid is the canonical person URL; the affiliation
        # FK is the canonical orgunit URL.
        pid = row.get("person_uuid")
        aff = row.get("primary_affiliation_uuid")
        row = {
            **row,
            "person_uuid": infoscience_person_iri(pid) or pid,
            "primary_affiliation_uuid": infoscience_org_iri(aff) or aff,
        }
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
        # v3.0.0: org_uuid + parent_org_uuid are canonical orgunit URLs.
        oid = row.get("org_uuid")
        pid = row.get("parent_org_uuid")
        row = {
            **row,
            "org_uuid": infoscience_org_iri(oid) or oid,
            "parent_org_uuid": infoscience_org_iri(pid) or pid,
        }
        if row.get("infoscience_url") is None and row.get("org_uuid"):
            row = {**row, "infoscience_url": row["org_uuid"]}
        self._upsert(
            table="organizations",
            cols=(
                "org_uuid",
                "name",
                "acronym",
                "infoscience_code",
                "unit_code",
                "infoscience_url",
                "parent_org_uuid",
                "parent_acronym",
                "director_name",
                "org_type_dspace",
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
        art_id = infoscience_article_iri(article_uuid) or article_uuid
        for person_uuid, position in person_positions:
            person_id = infoscience_person_iri(person_uuid) or person_uuid
            conn.execute(
                "INSERT INTO article_persons (article_uuid, person_uuid, position) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT (article_uuid, person_uuid) DO UPDATE SET position = "
                "COALESCE(LEAST(article_persons.position, excluded.position), "
                "         excluded.position, article_persons.position)",
                [art_id, person_id, position],
            )

    def upsert_article_orgs(
        self,
        article_uuid: str,
        org_field_pairs: Iterable[tuple[str, str]],
    ) -> None:
        conn = self.connect()
        art_id = infoscience_article_iri(article_uuid) or article_uuid
        for org_uuid, field in org_field_pairs:
            org_id = infoscience_org_iri(org_uuid) or org_uuid
            conn.execute(
                "INSERT INTO article_orgs (article_uuid, org_uuid, field) "
                "VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                [art_id, org_id, field],
            )

    def upsert_article_links(
        self,
        article_uuid: str,
        rows: Iterable[tuple[str, str, str]],
    ) -> None:
        """rows: iterable of (host_label, url, source)."""
        conn = self.connect()
        art_id = infoscience_article_iri(article_uuid) or article_uuid
        for host_label, url, source in rows:
            conn.execute(
                "INSERT INTO article_links (article_uuid, host_label, url, source) "
                "VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
                [art_id, host_label, url, source],
            )
