"""Filesystem layout for OpenAlex index artifacts.

Single source of truth for paths under `<INDEX_DATA_DIR>/openalex/`. Other
modules import from here rather than building paths ad-hoc.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_INDEX_DATA_DIR = Path("data/index")


def _resolve_repo_root() -> Path:
    """Return the repo root by walking up from this file."""
    return Path(__file__).resolve().parents[3]


def _resolve_index_data_dir() -> Path:
    """Resolve the shared index data dir (used by openalex + infoscience)."""
    raw = os.getenv("INDEX_DATA_DIR")
    if raw and raw.strip():
        candidate = Path(raw.strip()).expanduser()
        if candidate.is_absolute():
            return candidate
        return _resolve_repo_root() / candidate
    return _resolve_repo_root() / DEFAULT_INDEX_DATA_DIR


@dataclass(slots=True, frozen=True)
class OpenAlexPaths:
    """Resolved filesystem paths for the OpenAlex module."""

    root: Path

    @property
    def duckdb_dir(self) -> Path:
        return self.root / "duckdb"

    @property
    def duckdb_path(self) -> Path:
        return self.duckdb_dir / "openalex.duckdb"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"


def get_openalex_paths() -> OpenAlexPaths:
    """Resolve `<INDEX_DATA_DIR>/openalex/` and ensure subdirectories exist."""
    root = _resolve_index_data_dir() / "openalex"
    paths = OpenAlexPaths(root=root)
    paths.duckdb_dir.mkdir(parents=True, exist_ok=True)
    paths.cache_dir.mkdir(parents=True, exist_ok=True)
    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    return paths
