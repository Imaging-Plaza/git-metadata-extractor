"""Filesystem layout for the gitlab_ethz_projects index."""

from __future__ import annotations

from src.index._gitlab_base.paths_base import (
    GitLabIndexPathsBase,
    resolve_gitlab_paths,
)

GitLabEthzProjectsPaths = GitLabIndexPathsBase


def get_gitlab_ethz_projects_paths() -> GitLabEthzProjectsPaths:
    """Resolve ``<INDEX_DATA_DIR>/gitlab_ethz_projects/`` and ensure subdirs exist."""
    return resolve_gitlab_paths("gitlab_ethz_projects")
