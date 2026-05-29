"""Filesystem layout for the huggingface_papers index.

Reuses the account-base paths helper since the on-disk layout is
identical (duckdb/, cache/, logs/ subdirs) — only the index subdir
name and DuckDB filename change.
"""

from __future__ import annotations

from src.index._github_accounts_base.paths_base import (
    AccountIndexPathsBase,
    resolve_account_paths,
)

HuggingFacePapersPaths = AccountIndexPathsBase


def get_huggingface_papers_paths() -> HuggingFacePapersPaths:
    """Resolve `<INDEX_DATA_DIR>/huggingface_papers/` and ensure subdirs exist."""
    return resolve_account_paths(
        subdir="huggingface_papers",
        duckdb_filename="huggingface_papers.duckdb",
    )
