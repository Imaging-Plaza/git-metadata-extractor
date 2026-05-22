"""DuckDB lifecycle, schema bootstrap, and upsert helpers for Zenodo."""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb

from src.index.zenodo.paths import get_zenodo_paths

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

LOGGER = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

EMBEDDABLE_ENTITY_TYPES = {"records"}


def _load_schema_sql() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


class ZenodoStore:
    """Thin DuckDB wrapper for the Zenodo schema. `bootstrap()` is idempotent."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: duckdb.DuckDBPyConnection | None = None

    @classmethod
    def open(cls, db_path: Path | None = None) -> ZenodoStore:
        if db_path is None:
            db_path = get_zenodo_paths().duckdb_path
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
        # Migration: existing DBs created before `concept_recid` existed must
        # gain the column BEFORE schema.sql runs (schema.sql creates an index
        # on it). The backfill UPDATE must also run BEFORE the index is
        # created — DuckDB has hit internal errors when an UPDATE rewrites
        # every row of a freshly-created index in the same transaction.
        records_exists = (
            conn.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='main' AND table_name='records'",
            ).fetchone()
            is not None
        )
        if records_exists:
            cols = {
                r[1]
                for r in conn.execute("PRAGMA table_info('records')").fetchall()
            }
            if "concept_recid" not in cols:
                conn.execute("ALTER TABLE records ADD COLUMN concept_recid TEXT")
                conn.execute(
                    "UPDATE records SET concept_recid = "
                    "  CAST(json_extract_string(raw, '$.conceptrecid') AS TEXT) "
                    "WHERE raw IS NOT NULL "
                    "  AND json_extract_string(raw, '$.conceptrecid') IS NOT NULL",
                )
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

    # ---- Upserts ---------------------------------------------------------

    def upsert_record(self, row: dict[str, Any], raw: dict[str, Any]) -> None:
        # `community_ids` and `primary_community_id` are denormalised
        # community membership baked onto the record itself so
        # downstream queries can filter without joining
        # `record_communities`. The caller passes the community we're
        # currently crawling under as `primary_community_id`, plus the
        # full set of communities the record's raw payload mentions
        # as `community_ids` (deduped).
        community_ids = row.get("community_ids")
        if not isinstance(community_ids, list):
            community_ids = []
        sql = (
            "INSERT INTO records "
            "(zenodo_id, concept_recid, doi, title, description, publication_date, "
            " resource_type, access_right, license_id, keywords_json, "
            " community_ids, primary_community_id, raw, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (zenodo_id) DO UPDATE SET "
            "  concept_recid = excluded.concept_recid, "
            "  doi = excluded.doi, title = excluded.title, "
            "  description = excluded.description, "
            "  publication_date = excluded.publication_date, "
            "  resource_type = excluded.resource_type, "
            "  access_right = excluded.access_right, "
            "  license_id = excluded.license_id, "
            "  keywords_json = excluded.keywords_json, "
            "  community_ids = excluded.community_ids, "
            "  primary_community_id = COALESCE("
            "      records.primary_community_id, excluded.primary_community_id), "
            "  raw = excluded.raw, ingested_at = excluded.ingested_at"
        )
        self.connect().execute(
            sql,
            [
                row["zenodo_id"],
                row.get("concept_recid"),
                row.get("doi"),
                row.get("title"),
                row.get("description"),
                row.get("publication_date"),
                row.get("resource_type"),
                row.get("access_right"),
                row.get("license_id"),
                json.dumps(row.get("keywords") or [], ensure_ascii=False),
                json.dumps(community_ids, ensure_ascii=False),
                row.get("primary_community_id"),
                json.dumps(raw, ensure_ascii=False),
                self._now(),
            ],
        )

    def upsert_creator(self, row: dict[str, Any], raw: dict[str, Any]) -> None:
        sql = (
            "INSERT INTO creators "
            "(creator_key, display_name, orcid, affiliation, raw, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (creator_key) DO UPDATE SET "
            "  display_name = excluded.display_name, "
            "  orcid = excluded.orcid, "
            "  affiliation = excluded.affiliation, "
            "  raw = excluded.raw, ingested_at = excluded.ingested_at"
        )
        self.connect().execute(
            sql,
            [
                row["creator_key"],
                row.get("display_name"),
                row.get("orcid"),
                row.get("affiliation"),
                json.dumps(raw, ensure_ascii=False),
                self._now(),
            ],
        )

    def upsert_record_creators(
        self,
        record_id: str,
        creator_positions: Iterable[tuple[str, int]],
    ) -> None:
        conn = self.connect()
        for creator_key, position in creator_positions:
            conn.execute(
                "INSERT INTO record_creators (record_id, creator_key, position) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT (record_id, creator_key) DO UPDATE SET "
                "position = LEAST(record_creators.position, excluded.position)",
                [record_id, creator_key, position],
            )

    def upsert_community(self, row: dict[str, Any], raw: dict[str, Any]) -> None:
        sql = (
            "INSERT INTO communities (community_id, title, raw, ingested_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT (community_id) DO UPDATE SET "
            "  title = excluded.title, raw = excluded.raw, "
            "  ingested_at = excluded.ingested_at"
        )
        self.connect().execute(
            sql,
            [
                row["community_id"],
                row.get("title"),
                json.dumps(raw, ensure_ascii=False),
                self._now(),
            ],
        )

    def upsert_record_communities(
        self,
        record_id: str,
        community_ids: Iterable[str],
    ) -> None:
        conn = self.connect()
        for cid in community_ids:
            conn.execute(
                "INSERT INTO record_communities (record_id, community_id) "
                "VALUES (?, ?) ON CONFLICT DO NOTHING",
                [record_id, cid],
            )

    def upsert_file(self, row: dict[str, Any]) -> None:
        sql = (
            "INSERT INTO files "
            "(record_id, file_key, file_id, size_bytes, checksum, download_url) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (record_id, file_key) DO UPDATE SET "
            "  file_id = excluded.file_id, "
            "  size_bytes = excluded.size_bytes, "
            "  checksum = excluded.checksum, "
            "  download_url = excluded.download_url"
        )
        self.connect().execute(
            sql,
            [
                row["record_id"],
                row["file_key"],
                row.get("file_id"),
                row.get("size_bytes"),
                row.get("checksum"),
                row.get("download_url"),
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

    def existing_record_ids(self, zenodo_ids: list[str]) -> set[str]:
        """Return the subset of `zenodo_ids` already known to the store.

        Matches against either the canonical `zenodo_id` (post-redirect
        version-record) OR the `concept_recid` (Zenodo's "all versions"
        identifier). A discovery source typically extracts whichever ID
        appears in the citation, so checking both prevents redundant
        re-fetches when a paper cites the concept ID but we persisted
        the latest version.
        """
        if not zenodo_ids:
            return set()
        placeholders = ",".join(["?"] * len(zenodo_ids))
        cur = self.connect().execute(
            f"SELECT zenodo_id FROM records WHERE zenodo_id IN ({placeholders}) "
            f"UNION "
            f"SELECT concept_recid FROM records "
            f"WHERE concept_recid IN ({placeholders})",
            [*zenodo_ids, *zenodo_ids],
        )
        return {str(r[0]) for r in cur.fetchall() if r[0] is not None}

    def fetch_record(self, zenodo_id: str) -> dict[str, Any] | None:
        cur = self.connect().execute(
            "SELECT * FROM records WHERE zenodo_id = ?",
            [zenodo_id],
        )
        row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row, strict=False))

    def fetch_record_by_concept(self, concept_recid: str) -> dict[str, Any] | None:
        """Find any version-record under the given concept_recid.

        When a citation references the concept (parent) ID, our store only
        keeps the canonical version-record. This returns the most recent
        ingested version so callers can still resolve the citation.
        """
        cur = self.connect().execute(
            "SELECT * FROM records WHERE concept_recid = ? "
            "ORDER BY ingested_at DESC LIMIT 1",
            [concept_recid],
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
        """Yield rows that need embedding (no chunks yet)."""
        if entity_type not in EMBEDDABLE_ENTITY_TYPES:
            message = f"Unknown entity_type: {entity_type}"
            raise ValueError(message)
        # records.zenodo_id is the entity_id for the chunks table.
        sql = (
            "SELECT t.* FROM records t "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM chunks c "
            "  WHERE c.entity_type = ? AND c.entity_id = t.zenodo_id"
            ")"
        )
        params: list[Any] = [entity_type]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        cur = self.connect().execute(sql, params)
        cols = [d[0] for d in cur.description]
        # Materialize the full result up front: DuckDB's single-connection
        # model lets writes mid-iteration affect the NOT EXISTS predicate
        # if we stream lazily, so each upsert_chunk call would otherwise
        # filter out subsequent rows.
        rows = cur.fetchall()
        for row in rows:
            yield dict(zip(cols, row, strict=False))

    @staticmethod
    def _now() -> str:
        return datetime.now(tz=timezone.utc).isoformat()
