"""DuckDB lifecycle, schema bootstrap, and upsert helpers for the ROR index.

Mirrors the shape of `src/index/openalex/storage/duckdb_store.py` so a future
shared `src/index/_shared/` factor-up is mechanical. ROR-specific bits:

  - One DuckDB file per repo, three tables: `records` (full v2 dump),
    `scope_records` (per-scope membership + Qdrant point id), `manifests`.
  - `search_blob` is the lexical-lookup column — pre-folded (lowercase +
    accent-stripped) concatenation of names/aliases/acronyms — replacing
    `dump_index.py`'s in-memory inverted index with a `LIKE` over an
    already-normalised string.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import logging
import os
import re
import tempfile
import unicodedata
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import duckdb
from pydantic import BaseModel

from src.index.ror.paths import ror_data_dir

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

LOGGER = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _load_schema_sql() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


def default_db_path() -> Path:
    """`<INDEX_DATA_DIR>/ror/duckdb/ror.duckdb` — single-file shared by all scopes."""
    return ror_data_dir() / "duckdb" / "ror.duckdb"


# ---------------------------------------------------------------------------
# Search-blob helpers — also used by the migrator and tests.
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def fold_for_search(text: str) -> str:
    """Lowercase + drop combining marks (NFKD then drop Mn category).

    Matches the semantics of `dump_index.py:_fold`, so a record built into the
    DuckDB store is searchable by the same query strings the in-memory index
    accepted (e.g. `Universität` ↔ `Universitat`, `École` ↔ `Ecole`).
    """
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch)).lower()


def _names_grouped(record: dict[str, Any]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for entry in record.get("names") or []:
        if not isinstance(entry, dict):
            continue
        value = entry.get("value")
        if not isinstance(value, str) or not value:
            continue
        for t in entry.get("types") or []:
            grouped.setdefault(str(t), []).append(value)
    return grouped


def build_search_blob(record: dict[str, Any]) -> str:
    """Concatenate names + aliases + acronyms + label, return folded string."""
    parts: list[str] = []
    for entry in record.get("names") or []:
        if isinstance(entry, dict):
            value = entry.get("value")
            if isinstance(value, str) and value:
                parts.append(value)
    return " | ".join(fold_for_search(p) for p in parts)


_NAMESPACE_URL = uuid.NAMESPACE_URL


def vector_id_for(ror_id: str) -> str:
    """Deterministic UUIDv5 over the ROR URL — matches the existing Qdrant point id."""
    return str(uuid.uuid5(_NAMESPACE_URL, ror_id))


def _bare_id(value: str) -> str:
    return value.rstrip("/").rsplit("/", 1)[-1]


def _display_name(record: dict[str, Any]) -> Optional[str]:
    grouped = _names_grouped(record)
    if grouped.get("ror_display"):
        return grouped["ror_display"][0]
    if grouped.get("label"):
        return grouped["label"][0]
    for values in grouped.values():
        if values:
            return values[0]
    return None


def _first_location(record: dict[str, Any]) -> dict[str, Any]:
    locations = record.get("locations") or []
    if isinstance(locations, list) and locations and isinstance(locations[0], dict):
        details = locations[0].get("geonames_details") or {}
        if isinstance(details, dict):
            return details
    return {}


def _first_website(record: dict[str, Any]) -> Optional[str]:
    for link in record.get("links") or []:
        if isinstance(link, dict) and link.get("type") == "website":
            value = link.get("value")
            if isinstance(value, str) and value:
                return value
    return None


def extract_record_columns(
    record: dict[str, Any],
    *,
    ror_release_version: Optional[str] = None,
) -> dict[str, Any]:
    """Turn a raw ROR v2 record into the column dict accepted by `upsert_record`.

    Pure: no I/O, no DB calls. The migrator and the future `build.py` both
    funnel through this.
    """
    rid = record.get("id")
    if not isinstance(rid, str) or not rid:
        msg = "ROR record is missing a string `id`"
        raise ValueError(msg)
    rid = rid.rstrip("/")
    grouped = _names_grouped(record)
    location = _first_location(record)
    types_list = [str(t) for t in (record.get("types") or []) if t]
    return {
        "ror_id": rid,
        "ror_id_short": _bare_id(rid),
        "name": _display_name(record),
        "search_blob": build_search_blob(record),
        "status": record.get("status"),
        "country_code": location.get("country_code"),
        "country_name": location.get("country_name"),
        "city": location.get("name"),
        "region": location.get("country_subdivision_name") or location.get("region"),
        "established": record.get("established"),
        "website": _first_website(record),
        "types_json": types_list,
        "domains_json": record.get("domains") or [],
        "names_json": record.get("names") or [],
        "aliases_json": grouped.get("alias") or [],
        "acronyms_json": grouped.get("acronym") or [],
        "external_ids_json": record.get("external_ids") or [],
        "relationships_json": record.get("relationships") or [],
        "record": record,
        "ror_release_version": ror_release_version,
    }


# ---------------------------------------------------------------------------
# Pydantic carriers
# ---------------------------------------------------------------------------


class ScopeRecord(BaseModel):
    """One row of `scope_records`. Bridge to a Qdrant point."""

    scope_mode: str
    ror_id: str
    text: str
    vector_id: str
    embedded_at: Optional[str] = None


class StoreManifest(BaseModel):
    """One row of `manifests`. Mirrors `models.IndexManifest` minus the dual
    representation — kept separate so the storage layer doesn't import the
    sidecar-era models.
    """

    scope_mode: str
    record_count: int
    embedding_model: str
    embedding_dim: int
    reranker_model: str
    ror_release_version: Optional[str] = None
    ror_release_doi: Optional[str] = None
    built_at_iso: Optional[str] = None


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


_RECORD_COLS: tuple[str, ...] = (
    "ror_id",
    "ror_id_short",
    "name",
    "search_blob",
    "status",
    "country_code",
    "country_name",
    "city",
    "region",
    "established",
    "website",
    "types_json",
    "domains_json",
    "names_json",
    "aliases_json",
    "acronyms_json",
    "external_ids_json",
    "relationships_json",
    "record",
    "ror_release_version",
)

_JSON_COLS: frozenset[str] = frozenset(
    {
        "types_json",
        "domains_json",
        "names_json",
        "aliases_json",
        "acronyms_json",
        "external_ids_json",
        "relationships_json",
        "record",
    },
)


def _build_records_upsert_sql() -> str:
    cols = ", ".join(_RECORD_COLS)
    placeholders = ", ".join(["?"] * len(_RECORD_COLS))
    update_cols = ", ".join(f"{c} = excluded.{c}" for c in _RECORD_COLS[1:])
    return (
        f"INSERT INTO records ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT (ror_id) DO UPDATE SET "
        f"{update_cols}, ingested_at = now()"
    )


# Cached at import time — the column list never changes, so repeating the
# string concat per row in a 125k-record load is wasted work.
_RECORDS_UPSERT_SQL: str = _build_records_upsert_sql()

# Chunk size for batched ingest. Tuned so each transaction's undo log stays
# well under 100 MB even with the full record JSON in `record`.
_DEFAULT_CHUNK_SIZE: int = 2000


class DuckDBStore:
    """Thin wrapper around DuckDB tuned for the ROR schema.

    Construct with `DuckDBStore.open()` for the default repo path. Re-running
    `bootstrap()` is idempotent.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: duckdb.DuckDBPyConnection | None = None

    @classmethod
    def open(cls, db_path: Path | None = None) -> DuckDBStore:
        if db_path is None:
            db_path = default_db_path()
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
        # Promote the per-release Zenodo DOI to canonical URL form.
        from src.index._shared.doi import (  # noqa: PLC0415
            migrate_doi_column_to_url,
        )

        migrate_doi_column_to_url(
            conn, table="manifests", column="ror_release_doi",
        )

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
        inserts ~5–10× slower than batched ones. Wrapping ingest in this
        context manager folds them into a single commit — important for the
        ~125k-record full-dump load.
        """
        conn = self.connect()
        conn.execute("BEGIN TRANSACTION")
        try:
            yield
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")

    # ---- Writes ----------------------------------------------------------

    def upsert_record(self, columns: dict[str, Any]) -> None:
        """Upsert one row into `records`. `columns` shape from `extract_record_columns`."""
        self.connect().execute(_RECORDS_UPSERT_SQL, _row_values(columns))

    def upsert_records(self, rows: Iterable[dict[str, Any]]) -> int:
        """Bulk upsert. Returns the number of rows written.

        Caller controls the transaction boundary — wrap a small batch in
        `transaction()` for atomicity, or call `upsert_records_chunked()` for
        a 125k-row full-dump load (chunked commits keep memory bounded).
        """
        n = 0
        conn = self.connect()
        for columns in rows:
            conn.execute(_RECORDS_UPSERT_SQL, _row_values(columns))
            n += 1
        return n

    def bulk_replace_records(
        self,
        rows: Iterable[dict[str, Any]],
        *,
        csv_chunk_size: int = 50_000,
        progress: Optional[Any] = None,
    ) -> int:
        """Replace the `records` table via streaming `COPY FROM CSV`.

        The `INSERT … ON CONFLICT` path through DuckDB's Python binding caps
        out at ~18 rec/sec on this 20-column schema (measured), which would
        make the 125k-row full-dump load take ~2 hours. `COPY FROM` runs the
        whole load entirely inside DuckDB's vectorised executor and is
        ~230× faster on the same data — 125k rows in ~30 s.

        Workflow per call:
          1. `DELETE FROM records` — truncates atomically inside a transaction.
          2. Stream `rows` through a temp CSV file in chunks of
             `csv_chunk_size`, COPY each chunk, then delete the temp file.
          3. Commit.

        `progress`, if supplied, is called as `progress(rows_so_far)` after
        every successful COPY.

        Use this path for the full-dump load. For one-off / incremental
        writes (e.g. updating one record from `build.py`), use
        `upsert_record` / `upsert_records_chunked` — the slow path is fine
        when the volume is small.
        """
        if csv_chunk_size <= 0:
            msg = f"csv_chunk_size must be > 0, got {csv_chunk_size}"
            raise ValueError(msg)

        conn = self.connect()
        col_list = ", ".join(_RECORD_COLS)
        copy_sql = (
            f"COPY records ({col_list}) FROM ? "
            f"(FORMAT CSV, HEADER, DELIMITER ',', QUOTE '\"', ESCAPE '\"', NULLSTR '\\N', STRICT_MODE FALSE, PARALLEL FALSE)"
        )

        n = 0
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute("DELETE FROM records")
            buffer: list[dict[str, Any]] = []
            for columns in rows:
                buffer.append(columns)
                if len(buffer) >= csv_chunk_size:
                    self._copy_records_chunk(conn, buffer, copy_sql)
                    n += len(buffer)
                    buffer.clear()
                    if progress is not None:
                        progress(n)
            if buffer:
                self._copy_records_chunk(conn, buffer, copy_sql)
                n += len(buffer)
                if progress is not None:
                    progress(n)
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
        return n

    @staticmethod
    def _copy_records_chunk(
        conn: duckdb.DuckDBPyConnection,
        chunk: list[dict[str, Any]],
        copy_sql: str,
    ) -> None:
        # Materialise the chunk as a temp CSV, then run a single COPY.
        # `\N` is the NULL sentinel (matches the COPY clause above) — keeps
        # NULL distinguishable from the empty string for nullable columns
        # like `established`.
        fd, path = tempfile.mkstemp(prefix="ror_records_", suffix=".csv", dir=tempfile.gettempdir())
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f, quoting=csv.QUOTE_ALL)
                writer.writerow(_RECORD_COLS)
                for columns in chunk:
                    writer.writerow(_csv_row(columns))
            conn.execute(copy_sql, [path])
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def upsert_records_chunked(
        self,
        rows: Iterable[dict[str, Any]],
        *,
        chunk_size: int = _DEFAULT_CHUNK_SIZE,
        progress: Optional[Any] = None,
    ) -> int:
        """Bulk upsert with per-chunk transactions and optional progress hook.

        Each chunk is its own BEGIN/COMMIT — this caps DuckDB's in-memory
        undo log to one chunk's worth of rows, which is what makes a
        125k-record load fit in normal RAM.

        `progress`, if supplied, is called as `progress(rows_so_far)` after
        every commit.
        """
        if chunk_size <= 0:
            msg = f"chunk_size must be > 0, got {chunk_size}"
            raise ValueError(msg)
        conn = self.connect()
        n = 0
        buffer: list[dict[str, Any]] = []
        for columns in rows:
            buffer.append(columns)
            if len(buffer) >= chunk_size:
                self._flush_records_chunk(conn, buffer)
                n += len(buffer)
                buffer.clear()
                if progress is not None:
                    progress(n)
        if buffer:
            self._flush_records_chunk(conn, buffer)
            n += len(buffer)
            if progress is not None:
                progress(n)
        return n

    @staticmethod
    def _flush_records_chunk(
        conn: duckdb.DuckDBPyConnection,
        chunk: list[dict[str, Any]],
    ) -> None:
        # Single multi-row VALUES INSERT instead of N single-row inserts —
        # DuckDB's Python `executemany` was measured at ~18 rec/sec for the
        # ON CONFLICT upsert; this collapses N rows into one statement and
        # drops the per-row planning overhead.
        if not chunk:
            return
        n_cols = len(_RECORD_COLS)
        row_placeholder = "(" + ", ".join(["?"] * n_cols) + ")"
        values = ", ".join([row_placeholder] * len(chunk))
        col_list = ", ".join(_RECORD_COLS)
        update_cols = ", ".join(f"{c} = excluded.{c}" for c in _RECORD_COLS[1:])
        sql = (
            f"INSERT INTO records ({col_list}) VALUES {values} "
            f"ON CONFLICT (ror_id) DO UPDATE SET "
            f"{update_cols}, ingested_at = now()"
        )
        params: list[Any] = []
        for columns in chunk:
            params.extend(_row_values(columns))
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute(sql, params)
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")

    def set_scope_records(
        self, scope_mode: str, rows: Iterable[ScopeRecord],
    ) -> int:
        """Replace all rows for `scope_mode` via streaming COPY FROM CSV.

        Same bulk path as `bulk_replace_records` — necessary because the
        worldwide scope is 125k rows and the per-row INSERT path saturates
        at ~18 rec/sec on this binding.

        Returns the row count written.
        """
        conn = self.connect()
        embedded_default = _now_iso()
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute(
                "DELETE FROM scope_records WHERE scope_mode = ?", [scope_mode],
            )
            buffer: list[ScopeRecord] = []
            n = 0
            for row in rows:
                buffer.append(row)
                if len(buffer) >= 50_000:
                    self._copy_scope_records_chunk(conn, buffer, embedded_default)
                    n += len(buffer)
                    buffer.clear()
            if buffer:
                self._copy_scope_records_chunk(conn, buffer, embedded_default)
                n += len(buffer)
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
        return n

    @staticmethod
    def _copy_scope_records_chunk(
        conn: duckdb.DuckDBPyConnection,
        chunk: list[ScopeRecord],
        embedded_default: str,
    ) -> None:
        fd, path = tempfile.mkstemp(prefix="ror_scope_", suffix=".csv", dir=tempfile.gettempdir())
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f, quoting=csv.QUOTE_ALL)
                writer.writerow(["scope_mode", "ror_id", "text", "vector_id", "embedded_at"])
                for row in chunk:
                    writer.writerow([
                        row.scope_mode,
                        row.ror_id,
                        row.text,
                        row.vector_id,
                        row.embedded_at or embedded_default,
                    ])
            conn.execute(
                "COPY scope_records (scope_mode, ror_id, text, vector_id, embedded_at) "
                "FROM ? (FORMAT CSV, HEADER, DELIMITER ',', QUOTE '\"', ESCAPE '\"', NULLSTR '\\N', STRICT_MODE FALSE, PARALLEL FALSE)",
                [path],
            )
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def set_manifest(self, manifest: StoreManifest) -> None:
        sql = (
            "INSERT INTO manifests "
            "(scope_mode, record_count, embedding_model, embedding_dim, "
            " reranker_model, ror_release_version, ror_release_doi, built_at_iso) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (scope_mode) DO UPDATE SET "
            "record_count = excluded.record_count, "
            "embedding_model = excluded.embedding_model, "
            "embedding_dim = excluded.embedding_dim, "
            "reranker_model = excluded.reranker_model, "
            "ror_release_version = excluded.ror_release_version, "
            "ror_release_doi = excluded.ror_release_doi, "
            "built_at_iso = excluded.built_at_iso"
        )
        self.connect().execute(
            sql,
            [
                manifest.scope_mode,
                manifest.record_count,
                manifest.embedding_model,
                manifest.embedding_dim,
                manifest.reranker_model,
                manifest.ror_release_version,
                manifest.ror_release_doi,
                manifest.built_at_iso or _now_iso(),
            ],
        )

    # ---- Reads -----------------------------------------------------------

    def count_records(self) -> int:
        result = self.connect().execute("SELECT count(*) FROM records").fetchone()
        return int(result[0]) if result else 0

    def count_scope_records(self, scope_mode: str) -> int:
        result = self.connect().execute(
            "SELECT count(*) FROM scope_records WHERE scope_mode = ?",
            [scope_mode],
        ).fetchone()
        return int(result[0]) if result else 0

    def fetch_record(self, ror_id: str) -> Optional[dict[str, Any]]:
        """Exact match on ROR URL or bare id (`02s376052`). Returns the full
        record JSON merged with structured columns, or None."""
        rid = ror_id.strip().rstrip("/")
        bare = _bare_id(rid)
        cur = self.connect().execute(
            "SELECT * FROM records WHERE ror_id = ? OR ror_id_short = ? LIMIT 1",
            [rid, bare],
        )
        row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        return _hydrate_row(dict(zip(cols, row, strict=False)))

    def fetch_manifest(self, scope_mode: str) -> Optional[dict[str, Any]]:
        cur = self.connect().execute(
            "SELECT * FROM manifests WHERE scope_mode = ?", [scope_mode],
        )
        row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row, strict=False))

    def lookup(
        self,
        *,
        text: Optional[str] = None,
        ror_id: Optional[str] = None,
        country: Optional[str] = None,
        type_: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Lexical/exact lookup over the full `records` table.

        Returns the full hydrated rows (record JSON + structured columns).
        Mirrors `dump_index.DumpIndex.search` semantics:

          - `ror_id` (URL or bare): single hit or [].
          - `text`: tokenized, accent-folded; ranked by number of distinct
            tokens that appear in `search_blob`.
          - `country` / `type_` / `status`: payload filters; combinable.
        """
        conn = self.connect()

        if ror_id:
            hit = self.fetch_record(ror_id)
            return [hit] if hit else []

        where: list[str] = []
        params: list[Any] = []
        score_expr = "0"

        if text:
            tokens = _TOKEN_RE.findall(fold_for_search(text))
            if not tokens:
                return []
            score_terms: list[str] = []
            or_terms: list[str] = []
            for tok in tokens:
                like = f"%{tok}%"
                score_terms.append("(CASE WHEN search_blob LIKE ? THEN 1 ELSE 0 END)")
                params.append(like)
                or_terms.append("search_blob LIKE ?")
            # SCORE params first, then WHERE params.
            params.extend([f"%{t}%" for t in tokens])
            score_expr = " + ".join(score_terms)
            where.append("(" + " OR ".join(or_terms) + ")")

        if country:
            where.append("country_code = ?")
            params.append(country.upper())
        if type_:
            where.append("list_contains(CAST(types_json AS VARCHAR[]), ?)")
            params.append(type_)
        if status:
            where.append("status = ?")
            params.append(status)

        sql = f"SELECT *, ({score_expr}) AS _score FROM records"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY _score DESC, ror_id ASC LIMIT ?"
        params.append(int(limit))

        cur = conn.execute(sql, params)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        return [_hydrate_row(dict(zip(cols, r, strict=False))) for r in rows]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_values(columns: dict[str, Any]) -> list[Any]:
    out: list[Any] = []
    for col in _RECORD_COLS:
        v = columns.get(col)
        if col in _JSON_COLS:
            out.append(json.dumps(v if v is not None else None, ensure_ascii=False))
        else:
            out.append(v)
    return out


_NULL_SENTINEL = "\\N"


def _csv_row(columns: dict[str, Any]) -> list[str]:
    """CSV-encode one row. `None` renders as `\\N` (matches `NULLSTR '\\N'`)."""
    out: list[str] = []
    for col in _RECORD_COLS:
        v = columns.get(col)
        if v is None:
            out.append(_NULL_SENTINEL)
        elif col in _JSON_COLS:
            out.append(json.dumps(v, ensure_ascii=False))
        else:
            out.append(str(v))
    return out


def _hydrate_row(row: dict[str, Any]) -> dict[str, Any]:
    """Parse JSON-typed columns into Python values for caller convenience."""
    out = dict(row)
    for col in _JSON_COLS:
        if col in out and isinstance(out[col], str):
            try:
                out[col] = json.loads(out[col])
            except (TypeError, ValueError):
                pass
    return out


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()
