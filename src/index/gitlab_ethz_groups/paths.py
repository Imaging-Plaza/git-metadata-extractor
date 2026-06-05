"""Filesystem layout for the gitlab_ethz_groups index."""

from __future__ import annotations

from src.index._gitlab_base.paths_base import (
    GitLabIndexPathsBase,
    resolve_gitlab_paths,
)

GitLabEthzGroupsPaths = GitLabIndexPathsBase


def get_gitlab_ethz_groups_paths() -> GitLabEthzGroupsPaths:
    """Resolve ``<INDEX_DATA_DIR>/gitlab_ethz_groups/`` and ensure subdirs exist."""
    return resolve_gitlab_paths("gitlab_ethz_groups")
