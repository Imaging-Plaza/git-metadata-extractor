"""Filesystem layout for the huggingface_users index."""

from __future__ import annotations

from src.index._github_accounts_base.paths_base import (
    AccountIndexPathsBase,
    resolve_account_paths,
)

HuggingFaceUsersPaths = AccountIndexPathsBase


def get_huggingface_users_paths() -> HuggingFaceUsersPaths:
    return resolve_account_paths(
        subdir="huggingface_users",
        duckdb_filename="huggingface_users.duckdb",
    )
