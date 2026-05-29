"""Semantic search over the huggingface_models index."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from src.index._github_accounts_base.retrieval_base import (
    account_semantic_search_async,
)
from src.index.huggingface_models.embed.pipeline import MODELS_COLLECTION
from src.index.huggingface_models.storage.duckdb_store import (
    HuggingFaceModelsStore,
)

if TYPE_CHECKING:
    from src.index.huggingface_models.config import (
        HuggingFaceModelsIndexConfig,
    )

LOGGER = logging.getLogger(__name__)


def semantic_search(
    *,
    config: HuggingFaceModelsIndexConfig,
    query: str,
    top_k: int = 10,
    candidate_k: int = 50,
    filter_payload: dict[str, Any] | None = None,
    store: HuggingFaceModelsStore | None = None,
) -> list[dict[str, Any]]:
    if store is None:
        store = HuggingFaceModelsStore.open()
    return asyncio.run(
        account_semantic_search_async(
            config=config,
            collection=MODELS_COLLECTION,
            id_payload_key="repo_id",
            hydrate=store.fetch_model,
            query=query,
            top_k=top_k,
            candidate_k=candidate_k,
            filter_payload=filter_payload,
        ),
    )
