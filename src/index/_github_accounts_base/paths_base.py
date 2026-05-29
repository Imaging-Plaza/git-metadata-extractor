"""Shared filesystem layout for GitHub account indices.

Each concrete index module passes its subdirectory name (`github_users`
or `github_organizations`) and gets a fully-initialised paths dataclass
back. The subdir lives under `<INDEX_DATA_DIR>/` alongside the existing
`github`, `orcid`, etc. directories.
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
class AccountIndexPathsBase:
    """Filesystem layout shared by the user/org account indices.

    `duckdb_filename` lets each concrete subclass put its DB at e.g.
    `github_users.duckdb` rather than the parent dir's name + `.duckdb`.
    """

    root: Path
    duckdb_filename: str

    @property
    def duckdb_dir(self) -> Path:
        return self.root / "duckdb"

    @property
    def duckdb_path(self) -> Path:
        return self.duckdb_dir / self.duckdb_filename

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    @property
    def cache_db_path(self) -> Path:
        # Dedicated ProviderCache DB per index, same convention the repo
        # index uses — so wiping users' cache doesn't touch the repo or
        # v2 caches.
        return self.cache_dir / "providers.db"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"


def resolve_account_paths(
    *,
    subdir: str,
    duckdb_filename: str,
) -> AccountIndexPathsBase:
    """Build a paths dataclass for `<INDEX_DATA_DIR>/<subdir>/` and ensure
    the required subdirectories exist."""
    root = _resolve_index_data_dir() / subdir
    paths = AccountIndexPathsBase(root=root, duckdb_filename=duckdb_filename)
    paths.duckdb_dir.mkdir(parents=True, exist_ok=True)
    paths.cache_dir.mkdir(parents=True, exist_ok=True)
    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    return paths
