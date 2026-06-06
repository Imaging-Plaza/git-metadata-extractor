"""DuckDB store accessor for the gitlab_ethz_users index."""

from __future__ import annotations

from src.index._gitlab_base.user_store import GitLabUserStore
from src.index.gitlab_ethz_users.paths import get_gitlab_ethz_users_paths


def open_store() -> GitLabUserStore:
    """Open (and bootstrap) the gitlab_ethz_users DuckDB store."""
    return GitLabUserStore.open(get_gitlab_ethz_users_paths().duckdb_path)
