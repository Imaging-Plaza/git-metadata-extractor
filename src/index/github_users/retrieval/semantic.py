"""Semantic search over the github_users index."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from src.index._github_accounts_base.retrieval_base import (
    account_semantic_search_async,
)
from src.index.github_users.embed.pipeline import USERS_COLLECTION
from src.index.github_users.storage.duckdb_store import GitHubUsersStore

if TYPE_CHECKING:
    from src.index.github_users.config import GitHubUsersIndexConfig

LOGGER = logging.getLogger(__name__)


def semantic_search(
    *,
    config: GitHubUsersIndexConfig,
    query: str,
    top_k: int = 10,
    candidate_k: int = 50,
    filter_payload: dict[str, Any] | None = None,
    store: GitHubUsersStore | None = None,
) -> list[dict[str, Any]]:
    """Embed `query`, search the `github_users` collection, rerank,
    hydrate via DuckDB. Returns a list of {id, vector_score,
    rerank_score, payload, entity} dicts."""
    if store is None:
        store = GitHubUsersStore.open()
    return asyncio.run(
        account_semantic_search_async(
            config=config,
            collection=USERS_COLLECTION,
            id_payload_key="login",
            hydrate=store.fetch_user,
            query=query,
            top_k=top_k,
            candidate_k=candidate_k,
            filter_payload=filter_payload,
        ),
    )
