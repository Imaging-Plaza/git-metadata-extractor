"""Filesystem layout for GitHub index artifacts.

Single source of truth for paths under `<INDEX_DATA_DIR>/github/`. Mirrors
`src/index/zenodo/paths.py`.
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
class GitHubPaths:
    root: Path

    @property
    def duckdb_dir(self) -> Path:
        return self.root / "duckdb"

    @property
    def duckdb_path(self) -> Path:
        return self.duckdb_dir / "github_repos.duckdb"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    @property
    def cache_db_path(self) -> Path:
        # Dedicated ProviderCache DB so this index can be invalidated without
        # touching the v2 cache. Same SQLite schema as the v2 cache.
        return self.cache_dir / "providers.db"

    @property
    def cards_dir(self) -> Path:
        return self.root / "cards"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    def card_dir_for(self, owner: str, name: str) -> Path:
        return self.cards_dir / owner / name


def get_github_paths() -> GitHubPaths:
    """Resolve `<INDEX_DATA_DIR>/github/` and ensure subdirectories exist."""
    root = _resolve_index_data_dir() / "github_repos"
    paths = GitHubPaths(root=root)
    paths.duckdb_dir.mkdir(parents=True, exist_ok=True)
    paths.cache_dir.mkdir(parents=True, exist_ok=True)
    paths.cards_dir.mkdir(parents=True, exist_ok=True)
    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    return paths
