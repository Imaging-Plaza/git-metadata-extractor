"""End-to-end semantic retrieval: embed → vector search → rerank → hydrate."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from src.index.openalex.embed.rcp_client import RCPEmbeddingClient
from src.index.openalex.rerank.rcp_client import RCPRerankerClient
from src.index.openalex.storage.duckdb_store import OpenAlexStore
from src.index.openalex.vector.qdrant_store import QdrantStore

if TYPE_CHECKING:
    from src.index.openalex.config import OpenAlexIndexConfig

LOGGER = logging.getLogger(__name__)


async def _async_search(
    *,
    config: OpenAlexIndexConfig,
    query: str,
    entity_type: str,
    top_k: int,
    candidate_k: int,
    filter_payload: dict[str, Any] | None,
    store: OpenAlexStore,
) -> list[dict[str, Any]]:
    embed = RCPEmbeddingClient(config)
    qdrant = QdrantStore(config)
    rerank = RCPRerankerClient(config)

    [query_vec] = await embed.embed_all([query])
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
        # Reranker failed quietly — fall back to vector order.
        ordered = candidates[:top_k]
    else:
        ordered = []
        for r in reranked:
            cand = candidates[r["index"]]
            ordered.append(
                {**cand, "rerank_score": r["relevance_score"]},
            )

    hydrated = []
    for hit in ordered:
        payload = hit["payload"] or {}
        entity_id = payload.get("entity_id") or payload.get("openalex_id")
        if not entity_id:
            continue
        row = _hydrate(store, entity_type, entity_id)
        hydrated.append(
            {
                "id": hit["id"],
                "vector_score": hit["score"],
                "rerank_score": hit.get("rerank_score"),
                "payload": payload,
                "entity": row,
            },
        )
    return hydrated


def _payload_to_doc(payload: dict[str, Any]) -> str:
    title = payload.get("title")
    name = payload.get("display_name")
    parts = [p for p in (title, name) if p]
    return " — ".join(parts) if parts else json.dumps(payload, ensure_ascii=False)


def _hydrate(
    store: OpenAlexStore,
    entity_type: str,
    entity_id: str,
) -> dict[str, Any] | None:
    cur = store.connect().execute(
        f"SELECT * FROM {entity_type} WHERE openalex_id = ?",  # noqa: S608
        [entity_id],
    )
    row = cur.fetchone()
    if row is None:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row, strict=False))


def semantic_search(
    *,
    config: OpenAlexIndexConfig,
    query: str,
    entity_type: str = "works",
    top_k: int = 10,
    candidate_k: int = 50,
    filter_payload: dict[str, Any] | None = None,
    store: OpenAlexStore | None = None,
) -> list[dict[str, Any]]:
    """Synchronous entrypoint used by the CLI and the FastAPI app."""
    if store is None:
        store = OpenAlexStore.open()
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
