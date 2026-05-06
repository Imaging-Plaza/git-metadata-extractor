"""One-shot porter: legacy JSONL+manifest sidecars → DuckDB (D16, PR 3).

Walks `<INDEX_DATA_DIR>/ror/index/<scope>/` for each scope and folds the
existing `records.jsonl` + `manifest.json` into the DuckDB store. Also loads
the cached ROR v2 dump JSON into the `records` table so the lexical-lookup
path has the full ~125k records to query.

**Does not** call RCP and **does not** touch Qdrant beyond a read-only count
check at the end. Vectors stay where they are; the deterministic UUIDv5
`vector_id` we write into `scope_records` matches the existing Qdrant point
ids (built the same way at upsert time in `qdrant_store.py`).

Idempotent: re-running upserts records with `ON CONFLICT DO UPDATE` and
replaces `scope_records` for each scope as a unit.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from src.index.ror.config import RorIndexConfig
from src.index.ror.paths import dump_dir, index_data_root, manifest_path, records_path
from src.index.ror.qdrant_store import QdrantRorStore
from src.index.ror.storage.duckdb_store import (
    DuckDBStore,
    ScopeRecord,
    StoreManifest,
    extract_record_columns,
    vector_id_for,
)

LOGGER = logging.getLogger(__name__)

_VERSION_DIR_RE = re.compile(r"^v?\d+(\.\d+)*([_.-].*)?$")


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------


def list_scope_dirs() -> list[str]:
    base = index_data_root() / "ror" / "index"
    if not base.exists():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir())


def find_cached_dump_json() -> Optional[Path]:
    """Locate the most recent cached ROR v2 dump JSON.

    Returns the highest-version `*-ror-data.json` (or `_schema_v2.json`)
    under `<data_dir>/ror/dump/`. Returns None if no dump has been
    downloaded — caller should run `python -m src.index.ror build` first.
    """
    base = dump_dir()
    if not base.exists():
        return None
    versions = sorted(
        (p for p in base.iterdir() if p.is_dir() and _VERSION_DIR_RE.match(p.name)),
        key=lambda p: p.name,
        reverse=True,
    )
    for vdir in versions:
        for child in vdir.iterdir():
            if child.is_file() and child.name.endswith(".json") and "ror-data" in child.name:
                return child
    return None


def read_release_metadata(json_path: Path) -> dict[str, Any]:
    """Load `release.json` next to the dump JSON. Returns empty dict if absent."""
    meta = json_path.parent / "release.json"
    if not meta.exists():
        return {}
    try:
        return json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------------------
# Loaders (pure, no DB writes)
# ---------------------------------------------------------------------------


def iter_dump_records(json_path: Path) -> Iterator[dict[str, Any]]:
    """Stream the ROR v2 dump records.

    The dump is a single JSON array on disk. DuckDB can read it with
    `read_json_auto` for ingest, but we go through Python because we want
    `extract_record_columns` to compute the `search_blob` and structured
    columns deterministically (and to reuse the same shape `build.py` will
    use).
    """
    with json_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        msg = f"Expected ROR dump JSON to be a list at the top level: {json_path}"
        raise ValueError(msg)
    for record in data:
        if isinstance(record, dict):
            yield record


def iter_jsonl_records(jsonl_path: Path) -> Iterator[dict[str, Any]]:
    with jsonl_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


# ---------------------------------------------------------------------------
# Migration steps
# ---------------------------------------------------------------------------


def populate_full_dump(
    store: DuckDBStore,
    json_path: Path,
    *,
    release_version: Optional[str] = None,
    csv_chunk_size: int = 50_000,
) -> int:
    """Load all records from the cached ROR dump JSON into the `records` table.

    Uses `DuckDBStore.bulk_replace_records` (COPY FROM CSV) — ~230× faster
    than the per-row INSERT/UPSERT path (measured 4170 rec/sec vs 18 rec/sec
    on this schema). The `records` table is fully replaced inside a single
    transaction, so a partial run leaves the previous state intact.
    """
    LOGGER.info("Loading ROR dump from %s", json_path)

    def _columns_iter():
        for record in iter_dump_records(json_path):
            try:
                yield extract_record_columns(record, ror_release_version=release_version)
            except ValueError:
                LOGGER.warning("Skipping ROR record without id: %r", record.get("id"))
                continue

    n = store.bulk_replace_records(
        _columns_iter(),
        csv_chunk_size=csv_chunk_size,
        progress=lambda done: LOGGER.info(
            "Loaded %d records into `records` (COPY FROM CSV)", done,
        ),
    )
    LOGGER.info("Done — %d records in `records`", n)
    return n


def populate_scope(store: DuckDBStore, scope_mode: str) -> dict[str, Any]:
    """Port one scope's `records.jsonl` + `manifest.json` into DuckDB.

    Returns a summary dict including the row count written and the manifest.
    Raises `FileNotFoundError` if the scope's sidecar files are missing.
    """
    rp = records_path(scope_mode)
    mp = manifest_path(scope_mode)
    if not rp.exists():
        msg = f"records.jsonl not found for scope {scope_mode!r}: {rp}"
        raise FileNotFoundError(msg)
    if not mp.exists():
        msg = f"manifest.json not found for scope {scope_mode!r}: {mp}"
        raise FileNotFoundError(msg)

    manifest = json.loads(mp.read_text(encoding="utf-8"))

    rows: list[ScopeRecord] = []
    for entry in iter_jsonl_records(rp):
        rid = entry.get("ror_id")
        text = entry.get("text") or ""
        if not isinstance(rid, str) or not rid:
            continue
        rows.append(
            ScopeRecord(
                scope_mode=scope_mode,
                ror_id=rid.rstrip("/"),
                text=text,
                vector_id=vector_id_for(rid.rstrip("/")),
            ),
        )

    n = store.set_scope_records(scope_mode, rows)
    store.set_manifest(
        StoreManifest(
            scope_mode=scope_mode,
            record_count=int(manifest.get("record_count", n)),
            embedding_model=str(manifest.get("embedding_model", "")),
            embedding_dim=int(manifest.get("embedding_dim", 0)),
            reranker_model=str(manifest.get("reranker_model", "")),
            ror_release_version=manifest.get("ror_release_version"),
            ror_release_doi=manifest.get("ror_release_doi"),
            built_at_iso=manifest.get("built_at_iso"),
        ),
    )
    LOGGER.info("Migrated scope=%s rows=%d (jsonl→duckdb)", scope_mode, n)
    return {"scope_mode": scope_mode, "rows": n, "manifest": manifest}


def verify_against_qdrant(
    cfg: RorIndexConfig, store: DuckDBStore, scope_mode: str,
) -> dict[str, Any]:
    """Read-only Qdrant point count + DuckDB scope_records count, compared."""
    duck_count = store.count_scope_records(scope_mode)
    qstore = QdrantRorStore(cfg)
    try:
        qdrant_count = qstore.count(scope_mode)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning(
            "Could not read Qdrant count for scope=%s: %s. "
            "Migration of DuckDB succeeded; Qdrant comparison skipped.",
            scope_mode, exc,
        )
        qdrant_count = None
    match = (qdrant_count is not None) and (duck_count == qdrant_count)
    return {
        "scope_mode": scope_mode,
        "duckdb_count": duck_count,
        "qdrant_count": qdrant_count,
        "match": match,
    }


def migrate_all(
    cfg: RorIndexConfig,
    *,
    db_path: Optional[Path] = None,
    dump_path: Optional[Path] = None,
    skip_qdrant_check: bool = False,
) -> dict[str, Any]:
    """Run the full storage migration. Returns a summary suitable for printing."""
    store = DuckDBStore.open(db_path)

    json_path = dump_path or find_cached_dump_json()
    if json_path is None:
        msg = (
            "No cached ROR dump JSON found under <INDEX_DATA_DIR>/ror/dump/. "
            "Run `python -m src.index.ror build` once to download a dump first."
        )
        raise FileNotFoundError(msg)
    release_meta = read_release_metadata(json_path)
    release_version = release_meta.get("version") or _guess_version_from_path(json_path)
    record_count = populate_full_dump(store, json_path, release_version=release_version)

    scopes_summary: list[dict[str, Any]] = []
    for scope in list_scope_dirs():
        try:
            scopes_summary.append(populate_scope(store, scope))
        except FileNotFoundError as exc:
            LOGGER.warning("Skipping scope %s: %s", scope, exc)

    checks: list[dict[str, Any]] = []
    if not skip_qdrant_check:
        for entry in scopes_summary:
            checks.append(verify_against_qdrant(cfg, store, entry["scope_mode"]))

    store.close()
    return {
        "dump_path": str(json_path),
        "release_version": release_version,
        "records_loaded": record_count,
        "scopes": scopes_summary,
        "qdrant_checks": checks,
    }


def _guess_version_from_path(json_path: Path) -> Optional[str]:
    """Fallback when `release.json` is missing — read the parent dir name."""
    parent = json_path.parent.name
    return parent if _VERSION_DIR_RE.match(parent) else None


__all__ = [
    "find_cached_dump_json",
    "iter_dump_records",
    "iter_jsonl_records",
    "list_scope_dirs",
    "migrate_all",
    "populate_full_dump",
    "populate_scope",
    "read_release_metadata",
    "verify_against_qdrant",
]
