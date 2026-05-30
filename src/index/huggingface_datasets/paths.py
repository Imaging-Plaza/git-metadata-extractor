"""Filesystem layout for the huggingface_datasets index."""

from __future__ import annotations

from src.index._github_accounts_base.paths_base import (
    AccountIndexPathsBase,
    resolve_account_paths,
)

HuggingFaceDatasetsPaths = AccountIndexPathsBase


def get_huggingface_datasets_paths() -> HuggingFaceDatasetsPaths:
    return resolve_account_paths(
        subdir="huggingface_datasets",
        duckdb_filename="huggingface_datasets.duckdb",
    )
