"""Filesystem layout for the github_users index. Thin wrapper around
the shared paths base so callers can still write
`GitHubUsersPaths.duckdb_path` etc. for parallelism with the repo
index."""

from __future__ import annotations

from src.index._github_accounts_base.paths_base import (
    AccountIndexPathsBase,
    resolve_account_paths,
)

GitHubUsersPaths = AccountIndexPathsBase


def get_github_users_paths() -> GitHubUsersPaths:
    """Resolve `<INDEX_DATA_DIR>/github_users/` and ensure subdirs exist."""
    return resolve_account_paths(
        subdir="github_users",
        duckdb_filename="github_users.duckdb",
    )
