"""End-to-end semantic retrieval: embed → vector search → rerank → hydrate."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from src.index.orcid.embed.rcp_client import RCPEmbeddingClient
from src.index.orcid.rerank.rcp_client import RCPRerankerClient
from src.index.orcid.storage.duckdb_store import OrcidDuckDBStore
from src.index.orcid.vector.qdrant_store import OrcidQdrantStore

if TYPE_CHECKING:
    from src.index.orcid.config import OrcidIndexConfig

LOGGER = logging.getLogger(__name__)


async def _async_search(
    *,
    config: OrcidIndexConfig,
    query: str,
    entity_type: str,
    top_k: int,
    candidate_k: int,
    filter_payload: dict[str, Any] | None,
    store: OrcidDuckDBStore,
) -> list[dict[str, Any]]:
    embed = RCPEmbeddingClient(config)
    qdrant = OrcidQdrantStore(config)
    rerank = RCPRerankerClient(config)

    instructed = (
        f"Instruct: {config.rcp.query_instruction}\nQuery: {query}"
        if config.rcp.query_instruction
        else query
    )
    [query_vec] = await embed.embed_all([instructed])
    candidates = qdrant.search(
        entity_type,
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
        ordered = []
        for r in reranked:
            cand = candidates[r["index"]]
            ordered.append({**cand, "rerank_score": r["relevance_score"]})

    hydrated: list[dict[str, Any]] = []
    for hit in ordered:
        payload = hit["payload"] or {}
        orcid_id = payload.get("orcid_id")
        if not orcid_id:
            continue
        person = store.fetch_person(orcid_id)
        hydrated.append(
            {
                "id": hit["id"],
                "vector_score": hit["score"],
                "rerank_score": hit.get("rerank_score"),
                "payload": payload,
                "person": person,
            },
        )
    return hydrated


def _payload_to_doc(payload: dict[str, Any]) -> str:
    name = payload.get("display_name")
    org = payload.get("organization")
    parts = [p for p in (name, org) if p]
    return " — ".join(parts) if parts else json.dumps(payload, ensure_ascii=False)


def semantic_search(
    *,
    config: OrcidIndexConfig,
    query: str,
    entity_type: str = "persons",
    top_k: int = 10,
    candidate_k: int = 50,
    filter_payload: dict[str, Any] | None = None,
    store: OrcidDuckDBStore | None = None,
) -> list[dict[str, Any]]:
    """Synchronous entrypoint used by the CLI and the FastAPI app."""
    if store is None:
        store = OrcidDuckDBStore.open(scope=config.paths.scope)
    return asyncio.run(
        _async_search(
            config=config,
            query=query,
            entity_type=entity_type,
            top_k=top_k,
            candidate_k=candidate_k,
            filter_payload=filter_payload,
            store=store,
        ),
    )
