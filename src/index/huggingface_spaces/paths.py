"""Filesystem layout for the huggingface_spaces index."""

from __future__ import annotations

from src.index._github_accounts_base.paths_base import (
    AccountIndexPathsBase,
    resolve_account_paths,
)

HuggingFaceSpacesPaths = AccountIndexPathsBase


def get_huggingface_spaces_paths() -> HuggingFaceSpacesPaths:
    return resolve_account_paths(
        subdir="huggingface_spaces",
        duckdb_filename="huggingface_spaces.duckdb",
    )
