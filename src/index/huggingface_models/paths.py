"""Filesystem layout for the huggingface_models index."""

from __future__ import annotations

from src.index._github_accounts_base.paths_base import (
    AccountIndexPathsBase,
    resolve_account_paths,
)

HuggingFaceModelsPaths = AccountIndexPathsBase


def get_huggingface_models_paths() -> HuggingFaceModelsPaths:
    """Resolve `<INDEX_DATA_DIR>/huggingface_models/` and ensure subdirs exist."""
    return resolve_account_paths(
        subdir="huggingface_models",
        duckdb_filename="huggingface_models.duckdb",
    )
