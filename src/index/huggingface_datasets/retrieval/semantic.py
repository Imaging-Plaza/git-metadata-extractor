"""Semantic search over the huggingface_datasets index."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from src.index._github_accounts_base.retrieval_base import (
    account_semantic_search_async,
)
from src.index.huggingface_datasets.embed.pipeline import DATASETS_COLLECTION
from src.index.huggingface_datasets.storage.duckdb_store import (
    HuggingFaceDatasetsStore,
)

if TYPE_CHECKING:
    from src.index.huggingface_datasets.config import (
        HuggingFaceDatasetsIndexConfig,
    )

LOGGER = logging.getLogger(__name__)


def semantic_search(
    *,
    config: HuggingFaceDatasetsIndexConfig,
    query: str,
    top_k: int = 10,
    candidate_k: int = 50,
    filter_payload: dict[str, Any] | None = None,
    store: HuggingFaceDatasetsStore | None = None,
) -> list[dict[str, Any]]:
    if store is None:
        store = HuggingFaceDatasetsStore.open()
    return asyncio.run(
        account_semantic_search_async(
            config=config,
            collection=DATASETS_COLLECTION,
            id_payload_key="repo_id",
            hydrate=store.fetch_dataset,
            query=query,
            top_k=top_k,
            candidate_k=candidate_k,
            filter_payload=filter_payload,
        ),
    )
