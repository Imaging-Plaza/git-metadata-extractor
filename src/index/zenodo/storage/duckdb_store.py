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

    def upsert_record(self, row: dict[str, Any], raw: dict[str, Any]) -> None:
        sql = (
            "INSERT INTO records "
            "(zenodo_id, doi, title, description, publication_date, "
            " resource_type, access_right, license_id, keywords_json, raw, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (zenodo_id) DO UPDATE SET "
            "  doi = excluded.doi, title = excluded.title, "
            "  description = excluded.description, "
            "  publication_date = excluded.publication_date, "
            "  resource_type = excluded.resource_type, "
            "  access_right = excluded.access_right, "
            "  license_id = excluded.license_id, "
            "  keywords_json = excluded.keywords_json, "
            "  raw = excluded.raw, ingested_at = excluded.ingested_at"
        )
        self.connect().execute(
            sql,
            [
                row["zenodo_id"],
                row.get("doi"),
                row.get("title"),
                row.get("description"),
                row.get("publication_date"),
                row.get("resource_type"),
                row.get("access_right"),
                row.get("license_id"),
                json.dumps(row.get("keywords") or [], ensure_ascii=False),
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
