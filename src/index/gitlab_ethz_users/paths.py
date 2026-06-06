"""Filesystem layout for the gitlab_ethz_users index."""

from __future__ import annotations

from src.index._gitlab_base.paths_base import (
    GitLabIndexPathsBase,
    resolve_gitlab_paths,
)

GitLabEthzUsersPaths = GitLabIndexPathsBase


def get_gitlab_ethz_users_paths() -> GitLabEthzUsersPaths:
    """Resolve ``<INDEX_DATA_DIR>/gitlab_ethz_users/`` and ensure subdirs exist."""
    return resolve_gitlab_paths("gitlab_ethz_users")
