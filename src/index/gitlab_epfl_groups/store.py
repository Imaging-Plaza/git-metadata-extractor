"""DuckDB store accessor for the gitlab_epfl_groups index."""

from __future__ import annotations

from src.index._gitlab_base.group_store import GitLabGroupStore
from src.index.gitlab_epfl_groups.paths import get_gitlab_epfl_groups_paths


def open_store() -> GitLabGroupStore:
    """Open (and bootstrap) the gitlab_epfl_groups DuckDB store."""
    return GitLabGroupStore.open(get_gitlab_epfl_groups_paths().duckdb_path)
