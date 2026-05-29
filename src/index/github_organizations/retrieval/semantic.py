"""Semantic search over the github_organizations index."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from src.index._github_accounts_base.retrieval_base import (
    account_semantic_search_async,
)
from src.index.github_organizations.embed.pipeline import ORGS_COLLECTION
from src.index.github_organizations.storage.duckdb_store import (
    GitHubOrganizationsStore,
)

if TYPE_CHECKING:
    from src.index.github_organizations.config import (
        GitHubOrganizationsIndexConfig,
    )

LOGGER = logging.getLogger(__name__)


def semantic_search(
    *,
    config: GitHubOrganizationsIndexConfig,
    query: str,
    top_k: int = 10,
    candidate_k: int = 50,
    filter_payload: dict[str, Any] | None = None,
    store: GitHubOrganizationsStore | None = None,
) -> list[dict[str, Any]]:
    """Embed `query`, search the `github_organizations` collection,
    rerank, hydrate via DuckDB."""
    if store is None:
        store = GitHubOrganizationsStore.open()
    return asyncio.run(
        account_semantic_search_async(
            config=config,
            collection=ORGS_COLLECTION,
            id_payload_key="login",
            hydrate=store.fetch_organization,
            query=query,
            top_k=top_k,
            candidate_k=candidate_k,
            filter_payload=filter_payload,
        ),
    )
