"""Filesystem layout for the gitlab_epfl_users index."""

from __future__ import annotations

from src.index._gitlab_base.paths_base import (
    GitLabIndexPathsBase,
    resolve_gitlab_paths,
)

GitLabEpflUsersPaths = GitLabIndexPathsBase


def get_gitlab_epfl_users_paths() -> GitLabEpflUsersPaths:
    """Resolve ``<INDEX_DATA_DIR>/gitlab_epfl_users/`` and ensure subdirs exist."""
    return resolve_gitlab_paths("gitlab_epfl_users")
