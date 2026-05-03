"""Semantic retrieval over the SWISSUbase index.

Embed → Qdrant search (filterable by ``entity_type``) → RCP rerank →
DuckDB hydrate. Reuses the openalex RCP + Qdrant clients directly, same
duck-typing pattern as the zenodo index.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from src.index.openalex.embed.rcp_client import RCPEmbeddingClient
from src.index.openalex.rerank.rcp_client import RCPRerankerClient
from src.index.openalex.vector.qdrant_store import QdrantStore
from src.index.swissubase.embed.pipeline import SWISSUBASE_COLLECTION
from src.index.swissubase.storage.duckdb_store import SwissubaseStore

if TYPE_CHECKING:
    from src.index.swissubase.config import SwissubaseIndexConfig

LOGGER = logging.getLogger(__name__)


def _payload_to_doc(payload: dict[str, Any]) -> str:
    title = payload.get("title") or payload.get("display_name") or payload.get("name")
    if title:
        return str(title)
    return json.dumps(payload, ensure_ascii=False)


def _hydrate(store: SwissubaseStore, entity_type: str, entity_id: str) -> dict[str, Any] | None:
    if entity_type == "studies":
        return store.fetch_study(entity_id)
    if entity_type == "datasets":
        return store.fetch_dataset(entity_id)
    if entity_type == "persons":
        cur = store.connect().execute(
            "SELECT * FROM persons WHERE person_key = ?", [entity_id],
        )
        row = cur.fetchone()
        if row is None:
            return None
        return dict(zip([d[0] for d in cur.description], row, strict=False))
    if entity_type == "institutions":
        cur = store.connect().execute(
            "SELECT * FROM institutions WHERE institution_key = ?", [entity_id],
        )
        row = cur.fetchone()
        if row is None:
            return None
        return dict(zip([d[0] for d in cur.description], row, strict=False))
    return None


async def _async_search(
    *,
    config: SwissubaseIndexConfig,
    query: str,
    top_k: int,
    candidate_k: int,
    filter_payload: dict[str, Any] | None,
    store: SwissubaseStore,
) -> list[dict[str, Any]]:
    embed = RCPEmbeddingClient(config)  # type: ignore[arg-type]
    qdrant = QdrantStore(config)  # type: ignore[arg-type]
    rerank = RCPRerankerClient(config)  # type: ignore[arg-type]

    [query_vec] = await embed.embed_all([query])
    candidates = qdrant.search(
        SWISSUBASE_COLLECTION,
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
        entity_type = payload.get("entity_type")
        entity_id = payload.get("entity_id")
        if not (entity_type and entity_id):
            continue
        hydrated.append(
            {
                "id": hit["id"],
                "vector_score": hit["score"],
                "rerank_score": hit.get("rerank_score"),
                "payload": payload,
                "entity": _hydrate(store, str(entity_type), str(entity_id)),
            },
        )
    return hydrated


def semantic_search(
    *,
    config: SwissubaseIndexConfig,
    query: str,
    top_k: int = 10,
    candidate_k: int = 50,
    filter_payload: dict[str, Any] | None = None,
    store: SwissubaseStore | None = None,
) -> list[dict[str, Any]]:
    if store is None:
        store = SwissubaseStore.open()
    return asyncio.run(
        _async_search(
            config=config,
            query=query,
            top_k=top_k,
            candidate_k=candidate_k,
            filter_payload=filter_payload,
            store=store,
        ),
    )
