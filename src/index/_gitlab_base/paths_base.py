"""Shared filesystem layout for GitLab project indices.

Each concrete index module passes its store name (e.g. ``gitlab_epfl_projects``)
and gets a fully-initialised paths dataclass back. The store directory lives
under ``<INDEX_DATA_DIR>/`` alongside the other index subdirectories.
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
class GitLabIndexPathsBase:
    """Filesystem layout for a GitLab project index store.

    ``store_name`` is used both as the subdirectory name and to derive the
    DuckDB filename, e.g. ``gitlab_epfl_projects/duckdb/gitlab_epfl_projects.duckdb``.
    """

    root: Path
    store_name: str

    @property
    def duckdb_dir(self) -> Path:
        return self.root / "duckdb"

    @property
    def duckdb_path(self) -> Path:
        return self.duckdb_dir / f"{self.store_name}.duckdb"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    @property
    def cache_db_path(self) -> Path:
        return self.cache_dir / "providers.db"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"


def resolve_gitlab_paths(store_name: str) -> GitLabIndexPathsBase:
    """Build a paths dataclass for ``<INDEX_DATA_DIR>/<store_name>/`` and
    ensure the required subdirectories exist."""
    root = _resolve_index_data_dir() / store_name
    paths = GitLabIndexPathsBase(root=root, store_name=store_name)
    paths.duckdb_dir.mkdir(parents=True, exist_ok=True)
    paths.cache_dir.mkdir(parents=True, exist_ok=True)
    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    return paths
