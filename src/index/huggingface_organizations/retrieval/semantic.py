"""Semantic search over the huggingface_organizations index."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from src.index._github_accounts_base.retrieval_base import (
    account_semantic_search_async,
)
from src.index.huggingface_organizations.embed.pipeline import (
    ORGS_COLLECTION,
)
from src.index.huggingface_organizations.storage.duckdb_store import (
    HuggingFaceOrganizationsStore,
)

if TYPE_CHECKING:
    from src.index.huggingface_organizations.config import (
        HuggingFaceOrganizationsIndexConfig,
    )

LOGGER = logging.getLogger(__name__)


def semantic_search(
    *,
    config: HuggingFaceOrganizationsIndexConfig,
    query: str,
    top_k: int = 10,
    candidate_k: int = 50,
    filter_payload: dict[str, Any] | None = None,
    store: HuggingFaceOrganizationsStore | None = None,
) -> list[dict[str, Any]]:
    if store is None:
        store = HuggingFaceOrganizationsStore.open()
    return asyncio.run(
        account_semantic_search_async(
            config=config,
            collection=ORGS_COLLECTION,
            id_payload_key="slug",
            hydrate=store.fetch_organization,
            query=query,
            top_k=top_k,
            candidate_k=candidate_k,
            filter_payload=filter_payload,
        ),
    )
