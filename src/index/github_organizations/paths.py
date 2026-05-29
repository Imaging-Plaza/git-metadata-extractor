"""Filesystem layout for the github_organizations index."""

from __future__ import annotations

from src.index._github_accounts_base.paths_base import (
    AccountIndexPathsBase,
    resolve_account_paths,
)

GitHubOrganizationsPaths = AccountIndexPathsBase


def get_github_organizations_paths() -> GitHubOrganizationsPaths:
    """Resolve `<INDEX_DATA_DIR>/github_organizations/` and ensure subdirs exist."""
    return resolve_account_paths(
        subdir="github_organizations",
        duckdb_filename="github_organizations.duckdb",
    )
