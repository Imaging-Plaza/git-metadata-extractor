"""Semantic retrieval over the GitLab groups index.

Embed → Qdrant search → RCP rerank → DuckDB hydrate. Reuses the openalex
RCP + Qdrant clients directly.

The `collection` and `store` are parameters so each leaf (epfl, ethz,
datascience, com) can pass its own Qdrant collection and DuckDB store.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from src.index._rcp.embed_client import RCPEmbeddingClient
from src.index._rcp.reranker_client import RCPRerankerClient
from src.index.openalex.vector.qdrant_store import QdrantStore

if TYPE_CHECKING:
    from src.index._gitlab_base.group_store import GitLabGroupStore

LOGGER = logging.getLogger(__name__)


def _payload_to_doc(payload: dict[str, Any]) -> str:
    group_id = payload.get("group_id") or payload.get("entity_id")
    if group_id:
        return str(group_id)
    return json.dumps(payload, ensure_ascii=False)


async def _async_search(
    *,
    config: Any,
    collection: str,
    store: GitLabGroupStore,
    query: str,
    top_k: int,
    candidate_k: int,
    filter_payload: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    embed = RCPEmbeddingClient(config)  # type: ignore[arg-type]
    qdrant = QdrantStore(config)  # type: ignore[arg-type]
    rerank = RCPRerankerClient(config)  # type: ignore[arg-type]

    [query_vec] = await embed.embed_all([query])
    candidates = qdrant.search(
        collection,
        query_vector=query_vec,
        top_k=candidate_k,
        filter_payload=filter_payload,
    )
    if not candidates:
        return []

    docs = [_payload_to_doc(c["payload"]) for c in candidates]
    reranked = await rerank.rerank(query, docs, top_n=top_k)
    if not reranked:
        ordered = candidates[:top_k]
    else:
        ordered = [
            {**candidates[r["index"]], "rerank_score": r["relevance_score"]}
            for r in reranked
        ]

    hydrated: list[dict[str, Any]] = []
    for hit in ordered:
        payload = hit["payload"] or {}
        group_id = payload.get("group_id") or payload.get("entity_id")
        if not group_id:
            continue
        hydrated.append(
            {
                "id": hit["id"],
                "vector_score": hit["score"],
                "rerank_score": hit.get("rerank_score"),
                "payload": payload,
                "entity": store.fetch_group(str(group_id)),
            },
        )
    return hydrated


def group_semantic_search(
    *,
    config: Any,
    collection: str,
    store: GitLabGroupStore,
    query: str,
    top_k: int = 10,
    candidate_k: int = 50,
    filter_payload: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Search GitLab groups semantically in the given Qdrant collection."""
    return asyncio.run(
        _async_search(
            config=config,
            collection=collection,
            store=store,
            query=query,
            top_k=top_k,
            candidate_k=candidate_k,
            filter_payload=filter_payload,
        ),
    )
