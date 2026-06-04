"""Filesystem layout for the gitlab_datascience_projects index."""

from __future__ import annotations

from src.index._gitlab_base.paths_base import (
    GitLabIndexPathsBase,
    resolve_gitlab_paths,
)

GitLabDatascienceProjectsPaths = GitLabIndexPathsBase


def get_gitlab_datascience_projects_paths() -> GitLabDatascienceProjectsPaths:
    """Resolve ``<INDEX_DATA_DIR>/gitlab_datascience_projects/`` and ensure subdirs exist."""
    return resolve_gitlab_paths("gitlab_datascience_projects")
