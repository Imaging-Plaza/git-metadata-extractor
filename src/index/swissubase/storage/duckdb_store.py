"""DuckDB lifecycle, schema bootstrap, and upsert helpers for SWISSUbase."""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb

from src.index.swissubase.paths import get_swissubase_paths

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

LOGGER = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

EMBEDDABLE_ENTITY_TYPES = {"studies", "datasets", "persons", "institutions"}

# Per-entity-type SQL for streaming rows that need embedding (no chunks yet).
# Studies are gated on `affiliation_match = TRUE` so we only embed the
# in-scope subset (epfl_sdsc_ethz). Their child datasets are embedded only
# when the parent study is in scope.
_EMBEDDING_STREAM_SQL: dict[str, str] = {
    "studies": (
        "SELECT t.* FROM studies t "
        "WHERE t.affiliation_match = TRUE "
        "  AND NOT EXISTS ("
        "    SELECT 1 FROM chunks c "
        "    WHERE c.entity_type = 'studies' AND c.entity_id = t.study_id"
        "  )"
    ),
    "datasets": (
        "SELECT t.* FROM datasets t "
        "JOIN studies s ON s.study_id = t.study_id "
        "WHERE s.affiliation_match = TRUE "
        "  AND NOT EXISTS ("
        "    SELECT 1 FROM chunks c "
        "    WHERE c.entity_type = 'datasets' AND c.entity_id = t.dataset_id"
        "  )"
    ),
    "persons": (
        "SELECT DISTINCT t.* FROM persons t "
        "JOIN study_persons sp ON sp.person_key = t.person_key "
        "JOIN studies s ON s.study_id = sp.study_id "
        "WHERE s.affiliation_match = TRUE "
        "  AND NOT EXISTS ("
        "    SELECT 1 FROM chunks c "
        "    WHERE c.entity_type = 'persons' AND c.entity_id = t.person_key"
        "  )"
    ),
    "institutions": (
        "SELECT DISTINCT t.* FROM institutions t "
        "JOIN study_institutions si ON si.institution_key = t.institution_key "
        "JOIN studies s ON s.study_id = si.study_id "
        "WHERE s.affiliation_match = TRUE "
        "  AND NOT EXISTS ("
        "    SELECT 1 FROM chunks c "
        "    WHERE c.entity_type = 'institutions' AND c.entity_id = t.institution_key"
        "  )"
    ),
}


def _load_schema_sql() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


class SwissubaseStore:
    """Thin DuckDB wrapper for the SWISSUbase schema. `bootstrap()` is idempotent."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: duckdb.DuckDBPyConnection | None = None

    @classmethod
    def open(cls, db_path: Path | None = None) -> SwissubaseStore:
        if db_path is None:
            db_path = get_swissubase_paths().duckdb_path
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

    def upsert_study(
        self,
        row: dict[str, Any],
        *,
        raw_overview: dict[str, Any] | None,
        raw_dynamic_blocks: dict[str, Any] | None,
    ) -> None:
        sql = (
            "INSERT INTO studies "
            "(study_id, ref, title, description, description_language, "
            " start_date, end_date, progress, main_discipline, sub_discipline, "
            " version, data_availability, dataset_count, affiliation_match, "
            " source_url, raw_overview, raw_dynamic_blocks, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (study_id) DO UPDATE SET "
            "  ref = excluded.ref, title = excluded.title, "
            "  description = excluded.description, "
            "  description_language = excluded.description_language, "
            "  start_date = excluded.start_date, end_date = excluded.end_date, "
            "  progress = excluded.progress, "
            "  main_discipline = excluded.main_discipline, "
            "  sub_discipline = excluded.sub_discipline, "
            "  version = excluded.version, "
            "  data_availability = excluded.data_availability, "
            "  dataset_count = excluded.dataset_count, "
            "  affiliation_match = excluded.affiliation_match, "
            "  source_url = excluded.source_url, "
            "  raw_overview = COALESCE(excluded.raw_overview, studies.raw_overview), "
            "  raw_dynamic_blocks = COALESCE("
            "    excluded.raw_dynamic_blocks, studies.raw_dynamic_blocks"
            "  ), "
            "  ingested_at = excluded.ingested_at"
        )
        self.connect().execute(
            sql,
            [
                row["study_id"],
                row.get("ref"),
                row.get("title"),
                row.get("description"),
                row.get("description_language"),
                row.get("start_date"),
                row.get("end_date"),
                row.get("progress"),
                row.get("main_discipline"),
                row.get("sub_discipline"),
                row.get("version"),
                row.get("data_availability"),
                row.get("dataset_count"),
                bool(row.get("affiliation_match", False)),
                row["source_url"],
                json.dumps(raw_overview, ensure_ascii=False) if raw_overview else None,
                (
                    json.dumps(raw_dynamic_blocks, ensure_ascii=False)
                    if raw_dynamic_blocks else None
                ),
                self._now(),
            ],
        )

    def upsert_dataset(self, row: dict[str, Any], raw: dict[str, Any]) -> None:
        sql = (
            "INSERT INTO datasets "
            "(dataset_id, study_id, title, description, access_right, "
            " license_id, file_count, source_url, raw, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (dataset_id) DO UPDATE SET "
            "  study_id = excluded.study_id, title = excluded.title, "
            "  description = excluded.description, "
            "  access_right = excluded.access_right, "
            "  license_id = excluded.license_id, "
            "  file_count = excluded.file_count, "
            "  source_url = excluded.source_url, "
            "  raw = excluded.raw, ingested_at = excluded.ingested_at"
        )
        self.connect().execute(
            sql,
            [
                row["dataset_id"],
                row["study_id"],
                row.get("title"),
                row.get("description"),
                row.get("access_right"),
                row.get("license_id"),
                row.get("file_count"),
                row["source_url"],
                json.dumps(raw, ensure_ascii=False),
                self._now(),
            ],
        )

    def upsert_person(self, row: dict[str, Any], raw: dict[str, Any]) -> None:
        sql = (
            "INSERT INTO persons "
            "(person_key, display_name, orcid, affiliation, source_url, "
            " raw, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (person_key) DO UPDATE SET "
            "  display_name = excluded.display_name, "
            "  orcid = excluded.orcid, "
            "  affiliation = excluded.affiliation, "
            "  source_url = excluded.source_url, "
            "  raw = excluded.raw, ingested_at = excluded.ingested_at"
        )
        self.connect().execute(
            sql,
            [
                row["person_key"],
                row.get("display_name"),
                row.get("orcid"),
                row.get("affiliation"),
                row.get("source_url"),
                json.dumps(raw, ensure_ascii=False),
                self._now(),
            ],
        )

    def upsert_institution(self, row: dict[str, Any], raw: dict[str, Any]) -> None:
        sql = (
            "INSERT INTO institutions "
            "(institution_key, name, address, ror_id, source_url, "
            " raw, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (institution_key) DO UPDATE SET "
            "  name = excluded.name, address = excluded.address, "
            "  ror_id = excluded.ror_id, source_url = excluded.source_url, "
            "  raw = excluded.raw, ingested_at = excluded.ingested_at"
        )
        self.connect().execute(
            sql,
            [
                row["institution_key"],
                row.get("name"),
                row.get("address"),
                row.get("ror_id"),
                row.get("source_url"),
                json.dumps(raw, ensure_ascii=False),
                self._now(),
            ],
        )

    def upsert_study_persons(
        self,
        study_id: str,
        entries: Iterable[tuple[str, str | None, int]],
    ) -> None:
        conn = self.connect()
        for person_key, role, position in entries:
            conn.execute(
                "INSERT INTO study_persons (study_id, person_key, role, position) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT (study_id, person_key, role) DO UPDATE SET "
                "  position = LEAST(study_persons.position, excluded.position)",
                [study_id, person_key, role or "", position],
            )

    def upsert_study_institutions(
        self,
        study_id: str,
        institution_keys: Iterable[str],
    ) -> None:
        conn = self.connect()
        for ikey in institution_keys:
            conn.execute(
                "INSERT INTO study_institutions (study_id, institution_key) "
                "VALUES (?, ?) ON CONFLICT DO NOTHING",
                [study_id, ikey],
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

    def existing_study_ids(self, study_ids: list[str]) -> set[str]:
        if not study_ids:
            return set()
        placeholders = ",".join(["?"] * len(study_ids))
        cur = self.connect().execute(
            f"SELECT study_id FROM studies WHERE study_id IN ({placeholders})",
            study_ids,
        )
        return {str(r[0]) for r in cur.fetchall()}

    def fetch_study(self, study_id: str) -> dict[str, Any] | None:
        cur = self.connect().execute(
            "SELECT * FROM studies WHERE study_id = ?",
            [study_id],
        )
        row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row, strict=False))

    def fetch_dataset(self, dataset_id: str) -> dict[str, Any] | None:
        cur = self.connect().execute(
            "SELECT * FROM datasets WHERE dataset_id = ?",
            [dataset_id],
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
        sql = _EMBEDDING_STREAM_SQL[entity_type]
        params: list[Any] = []
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        cur = self.connect().execute(sql, params)
        cols = [d[0] for d in cur.description]
        # Materialize: same reason as zenodo's note — DuckDB's single-conn
        # model lets concurrent upserts affect the NOT EXISTS predicate
        # mid-stream and silently filter out remaining rows.
        rows = cur.fetchall()
        for row in rows:
            yield dict(zip(cols, row, strict=False))

    @staticmethod
    def _now() -> str:
        return datetime.now(tz=timezone.utc).isoformat()
