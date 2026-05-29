"""Shared semantic-search helper for the github user/org indices.

Embed → Qdrant search → RCP rerank → DuckDB hydrate. Each concrete
index passes its collection name + a `hydrate` callback that takes the
account id (login) and returns the full DuckDB row.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Callable

from src.index.openalex.embed.rcp_client import RCPEmbeddingClient
from src.index.openalex.rerank.rcp_client import RCPRerankerClient
from src.index.openalex.vector.qdrant_store import QdrantStore

if TYPE_CHECKING:
    from src.index._github_accounts_base.config_base import AccountIndexConfigBase

LOGGER = logging.getLogger(__name__)


def _payload_to_doc(payload: dict[str, Any], *, id_payload_key: str) -> str:
    """Build the doc string fed to the reranker. Falls back to a
    JSON-encoded payload if the id key is missing — same shape as the
    repo index."""
    candidate = payload.get(id_payload_key) or payload.get("entity_id")
    if candidate:
        return str(candidate)
    return json.dumps(payload, ensure_ascii=False)


async def account_semantic_search_async(
    *,
    config: AccountIndexConfigBase,
    collection: str,
    id_payload_key: str,
    hydrate: Callable[[str], dict[str, Any] | None],
    query: str,
    top_k: int,
    candidate_k: int,
    filter_payload: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Embed `query`, search `collection`, rerank, hydrate via `hydrate`.

    Returns a list of {id, vector_score, rerank_score, payload, entity}
    dicts — same shape the repo index returns, so the v2 search wrapper
    `hit_from_raw` works against it unchanged.
    """
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

    docs = [_payload_to_doc(c["payload"], id_payload_key=id_payload_key) for c in candidates]
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
        entity_id = payload.get(id_payload_key) or payload.get("entity_id")
        if not entity_id:
            continue
        hydrated.append(
            {
                "id": hit["id"],
                "vector_score": hit["score"],
                "rerank_score": hit.get("rerank_score"),
                "payload": payload,
                "entity": hydrate(str(entity_id)),
            },
        )
    return hydrated
