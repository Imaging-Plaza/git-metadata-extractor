"""Semantic search over the huggingface_users index."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from src.index._github_accounts_base.retrieval_base import (
    account_semantic_search_async,
)
from src.index.huggingface_users.embed.pipeline import USERS_COLLECTION
from src.index.huggingface_users.storage.duckdb_store import (
    HuggingFaceUsersStore,
)

if TYPE_CHECKING:
    from src.index.huggingface_users.config import (
        HuggingFaceUsersIndexConfig,
    )

LOGGER = logging.getLogger(__name__)


def semantic_search(
    *,
    config: HuggingFaceUsersIndexConfig,
    query: str,
    top_k: int = 10,
    candidate_k: int = 50,
    filter_payload: dict[str, Any] | None = None,
    store: HuggingFaceUsersStore | None = None,
) -> list[dict[str, Any]]:
    if store is None:
        store = HuggingFaceUsersStore.open()
    return asyncio.run(
        account_semantic_search_async(
            config=config,
            collection=USERS_COLLECTION,
            id_payload_key="slug",
            hydrate=store.fetch_user,
            query=query,
            top_k=top_k,
            candidate_k=candidate_k,
            filter_payload=filter_payload,
        ),
    )
