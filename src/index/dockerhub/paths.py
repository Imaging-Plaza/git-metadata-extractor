"""Filesystem layout for Docker Hub index artifacts.

Single source of truth for paths under `<INDEX_DATA_DIR>/dockerhub/`.
Mirrors `src/index/github_repos/paths.py` (minus the README cards dir —
Docker Hub serves the full description inline in the repo metadata, so
there is no separate card file to persist).
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
class DockerhubPaths:
    root: Path

    @property
    def duckdb_dir(self) -> Path:
        return self.root / "duckdb"

    @property
    def duckdb_path(self) -> Path:
        return self.duckdb_dir / "dockerhub.duckdb"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    @property
    def cache_db_path(self) -> Path:
        # Dedicated ProviderCache DB so this index can be invalidated
        # without touching the v2 cache. Same SQLite schema as the v2 cache.
        return self.cache_dir / "providers.db"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"


def get_dockerhub_paths() -> DockerhubPaths:
    """Resolve `<INDEX_DATA_DIR>/dockerhub/` and ensure subdirectories exist."""
    root = _resolve_index_data_dir() / "dockerhub"
    paths = DockerhubPaths(root=root)
    paths.duckdb_dir.mkdir(parents=True, exist_ok=True)
    paths.cache_dir.mkdir(parents=True, exist_ok=True)
    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    return paths
