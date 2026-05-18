"""Filesystem layout for OAM-CH index artifacts.

Single source of truth for paths under ``<INDEX_DATA_DIR>/oamonitor/``.
Mirrors the layout used by zenodo / openalex / huggingface so the harness
applies uniformly across indices.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_INDEX_DATA_DIR = Path("data/index")


def _resolve_repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _resolve_index_data_dir() -> Path:
    raw = os.getenv("INDEX_DATA_DIR")
    if raw and raw.strip():
        candidate = Path(raw.strip()).expanduser()
        if candidate.is_absolute():
            return candidate
        return _resolve_repo_root() / candidate
    return _resolve_repo_root() / DEFAULT_INDEX_DATA_DIR


@dataclass(slots=True, frozen=True)
class OamonitorPaths:
    root: Path

    @property
    def duckdb_dir(self) -> Path:
        return self.root / "duckdb"

    @property
    def duckdb_path(self) -> Path:
        return self.duckdb_dir / "oamonitor.duckdb"

    @property
    def state_dir(self) -> Path:
        return self.root / "state"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"


def get_oamonitor_paths() -> OamonitorPaths:
    """Resolve ``<INDEX_DATA_DIR>/oamonitor/`` and ensure subdirectories exist."""
    root = _resolve_index_data_dir() / "oamonitor"
    paths = OamonitorPaths(root=root)
    paths.duckdb_dir.mkdir(parents=True, exist_ok=True)
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    paths.cache_dir.mkdir(parents=True, exist_ok=True)
    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    return paths
