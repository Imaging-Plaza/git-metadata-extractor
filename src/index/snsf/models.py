"""Pydantic models for the SNSF P3 index.

Phase 1 only needs lightweight carriers — the heavy lifting is done by
DuckDB SQL inside `storage.duckdb_store.SnsfStore.load_*` (no row-by-row
Python iteration over the 90 k-grant dump).
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class IngestSummary(BaseModel):
    """Returned by `local_ingest.run` and surfaced by the CLI."""

    source_dir: str
    grants_loaded: int
    persons_loaded: int
    disciplines_loaded: int
    outputs_loaded: dict[str, int] = {}
    scope_mode: str
    scope_grants: int
    snapshot_iso: Optional[str] = None


class IngestManifest(BaseModel):
    """One row of `manifests`."""

    scope_mode: str
    record_count: int
    snapshot_iso: Optional[str] = None
    source_dir: Optional[str] = None
    built_at_iso: Optional[str] = None
