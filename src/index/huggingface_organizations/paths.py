"""Filesystem layout for the huggingface_organizations index."""

from __future__ import annotations

from src.index._github_accounts_base.paths_base import (
    AccountIndexPathsBase,
    resolve_account_paths,
)

HuggingFaceOrganizationsPaths = AccountIndexPathsBase


def get_huggingface_organizations_paths() -> HuggingFaceOrganizationsPaths:
    return resolve_account_paths(
        subdir="huggingface_organizations",
        duckdb_filename="huggingface_organizations.duckdb",
    )
