"""DuckDB store accessor for the gitlab_epfl_users index."""

from __future__ import annotations

from src.index._gitlab_base.user_store import GitLabUserStore
from src.index.gitlab_epfl_users.paths import get_gitlab_epfl_users_paths


def open_store() -> GitLabUserStore:
    """Open (and bootstrap) the gitlab_epfl_users DuckDB store."""
    return GitLabUserStore.open(get_gitlab_epfl_users_paths().duckdb_path)
