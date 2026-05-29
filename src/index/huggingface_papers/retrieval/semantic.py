"""Semantic search over the huggingface_papers index."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from src.index._github_accounts_base.retrieval_base import (
    account_semantic_search_async,
)
from src.index.huggingface_papers.embed.pipeline import PAPERS_COLLECTION
from src.index.huggingface_papers.storage.duckdb_store import (
    HuggingFacePapersStore,
)

if TYPE_CHECKING:
    from src.index.huggingface_papers.config import HuggingFacePapersIndexConfig

LOGGER = logging.getLogger(__name__)


def semantic_search(
    *,
    config: HuggingFacePapersIndexConfig,
    query: str,
    top_k: int = 10,
    candidate_k: int = 50,
    filter_payload: dict[str, Any] | None = None,
    store: HuggingFacePapersStore | None = None,
) -> list[dict[str, Any]]:
    """Embed `query`, search the `huggingface_papers` collection,
    rerank, hydrate via DuckDB."""
    if store is None:
        store = HuggingFacePapersStore.open()
    return asyncio.run(
        account_semantic_search_async(
            config=config,
            collection=PAPERS_COLLECTION,
            id_payload_key="arxiv_id",
            hydrate=store.fetch_paper,
            query=query,
            top_k=top_k,
            candidate_k=candidate_k,
            filter_payload=filter_payload,
        ),
    )
