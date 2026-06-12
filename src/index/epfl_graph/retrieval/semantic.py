"""Semantic search over the EPFL Graph disciplines collection.

Embed query → Qdrant top-K vector search → optional RCP rerank →
DuckDB hydrate (parent chain). Returns small dicts ready for callers
(agent tools, federated layer, CLI).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from src.index.epfl_graph.embed.pipeline import EPFL_GRAPH_COLLECTION
from src.index.epfl_graph.storage.duckdb_store import EpflGraphStore
from src.index._rcp.embed_client import RCPEmbeddingClient
from src.index._rcp.reranker_client import RCPRerankerClient
from src.index.openalex.vector.qdrant_store import QdrantStore

if TYPE_CHECKING:
    from src.index.epfl_graph.config import EpflGraphIndexConfig

LOGGER = logging.getLogger(__name__)


def _payload_to_doc(payload: dict[str, Any]) -> str:
    name = payload.get("name") or payload.get("category_id")
    text = payload.get("embedding_text")
    if isinstance(text, str) and text.strip():
        return text
    return str(name) if name else ""


def _walk_chain(
    store: EpflGraphStore, leaf_id: str, max_depth: int = 10,
) -> list[dict[str, Any]]:
    chain: list[dict[str, Any]] = []
    seen: set[str] = set()
    current = leaf_id
    while current and current not in seen and len(chain) < max_depth:
        record = store.fetch_category(current)
        if not record:
            break
        chain.append(
            {
                "category_id": record["category_id"],
                "name": record.get("name"),
                "depth": record.get("depth"),
                "wikipedia_url": record.get("wikipedia_url"),
            },
        )
        seen.add(current)
        parent = record.get("parent_id")
        if parent in {None, "", "root"}:
            break
        current = parent
    return chain


async def _async_search(  # noqa: PLR0913
    *,
    config: EpflGraphIndexConfig,
    query: str,
    top_k: int,
    candidate_k: int,
    min_depth: int | None,
    rerank: bool,
    store: EpflGraphStore,
) -> list[dict[str, Any]]:
    config.require_rcp()
    embed_client = RCPEmbeddingClient(config)  # type: ignore[arg-type]
    qdrant = QdrantStore(config)  # type: ignore[arg-type]

    [query_vec] = await embed_client.embed_all([query])
    candidates = qdrant.search(
        EPFL_GRAPH_COLLECTION,
        query_vector=query_vec,
        top_k=candidate_k,
        filter_payload=None,
    )
    if not candidates:
        return []

    if min_depth is not None:
        candidates = [
            c for c in candidates
            if isinstance(c.get("payload"), dict)
            and isinstance(c["payload"].get("depth"), int)
            and c["payload"]["depth"] >= min_depth
        ]
    if not candidates:
        return []

    if rerank:
        rerank_client = RCPRerankerClient(config)  # type: ignore[arg-type]
        docs = [_payload_to_doc(c["payload"]) for c in candidates]
        reranked = await rerank_client.rerank(query, docs, top_n=top_k)
        if reranked:
            ordered = [
                {
                    **candidates[r["index"]],
                    "rerank_score": r["relevance_score"],
                }
                for r in reranked
            ]
        else:
            ordered = candidates[:top_k]
    else:
        ordered = candidates[:top_k]

    out: list[dict[str, Any]] = []
    for hit in ordered:
        payload = hit.get("payload") or {}
        category_id = payload.get("category_id") or payload.get("entity_id")
        if not category_id:
            continue
        out.append(
            {
                "category_id": str(category_id),
                "name": payload.get("name"),
                "depth": payload.get("depth"),
                "wikipedia_url": payload.get("wikipedia_url"),
                "graphsearch_url": payload.get("graphsearch_url"),
                "vector_score": hit.get("score"),
                "rerank_score": hit.get("rerank_score"),
                "payload": payload,
                "chain": _walk_chain(store, str(category_id)),
            },
        )
    return out


def semantic_search(  # noqa: PLR0913
    *,
    config: EpflGraphIndexConfig,
    query: str,
    top_k: int = 10,
    candidate_k: int = 50,
    min_depth: int | None = None,
    rerank: bool = True,
) -> list[dict[str, Any]]:
    """Find the top disciplines closest to ``query``.

    ``min_depth`` filters out shallow ancestor categories — set it to 4
    to keep only leaf-level disciplines, or leave it None to mirror the
    config's ``filter.min_depth`` (which already gated the embed pass).
    """
    # Read-only: search only SELECTs, and a read-write handle here collides with
    # the concurrent read-only disciplines lookup during extraction (Bug 01).
    store = EpflGraphStore.open_readonly(config.paths.duckdb_path)
    try:
        return asyncio.run(
            _async_search(
                config=config,
                query=query,
                top_k=top_k,
                candidate_k=candidate_k,
                min_depth=min_depth,
                rerank=rerank,
                store=store,
            ),
        )
    finally:
        store.close()
