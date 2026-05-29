"""Semantic retrieval over the OAM-CH index.

Embed → Qdrant search → RCP rerank → DuckDB hydrate. ``entity_type``
selects which Qdrant collection / DuckDB table to query. Reuses the
openalex RCP and Qdrant clients via duck typing on the config sub-blocks.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from src.index.oamonitor.embed.pipeline import OAM_COLLECTIONS, qdrant_collection_for
from src.index.oamonitor.storage.duckdb_store import ENTITY_TABLES, OamonitorStore
from src.index._rcp.embed_client import RCPEmbeddingClient
from src.index._rcp.reranker_client import RCPRerankerClient
from src.index.openalex.vector.qdrant_store import QdrantStore

if TYPE_CHECKING:
    from src.index.oamonitor.config import OamonitorIndexConfig

LOGGER = logging.getLogger(__name__)

_VALID_ENTITIES: frozenset[str] = frozenset(OAM_COLLECTIONS.keys())


def _payload_to_doc(payload: dict[str, Any]) -> str:
    text = payload.get("embedding_text")
    if isinstance(text, str) and text:
        return text
    return json.dumps(payload, ensure_ascii=False)


def _hydrate(
    store: OamonitorStore, entity_type: str, entity_id: str,
) -> dict[str, Any] | None:
    """Reads the raw JSON blob back out of DuckDB for inclusion on the hit."""
    if entity_type not in ENTITY_TABLES:
        return None
    cursor = store.connect().execute(
        f"SELECT raw FROM {entity_type} WHERE _id = ?",  # noqa: S608
        [entity_id],
    ).fetchone()
    if not cursor or cursor[0] is None:
        return None
    raw = cursor[0]
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
    if isinstance(raw, dict):
        return raw
    return None


async def _async_search(
    *,
    config: OamonitorIndexConfig,
    query: str,
    entity_type: str,
    top_k: int,
    candidate_k: int,
    filter_payload: dict[str, Any] | None,
    store: OamonitorStore,
) -> list[dict[str, Any]]:
    embed = RCPEmbeddingClient(config)  # type: ignore[arg-type]
    qdrant = QdrantStore(config)  # type: ignore[arg-type]
    rerank = RCPRerankerClient(config)  # type: ignore[arg-type]

    [query_vec] = await embed.embed_all([query])
    candidates = qdrant.search(
        qdrant_collection_for(entity_type),
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
        entity_id = payload.get("entity_id")
        if not isinstance(entity_id, str):
            continue
        hydrated.append(
            {
                "id": hit["id"],
                "vector_score": hit["score"],
                "rerank_score": hit.get("rerank_score"),
                "payload": payload,
                "entity": _hydrate(store, entity_type, entity_id),
            },
        )
    return hydrated


def semantic_search(
    *,
    config: OamonitorIndexConfig,
    query: str,
    entity_type: str = "journals",
    top_k: int = 10,
    candidate_k: int = 50,
    filter_payload: dict[str, Any] | None = None,
    store: OamonitorStore | None = None,
) -> list[dict[str, Any]]:
    if entity_type not in _VALID_ENTITIES:
        message = (
            f"Unsupported OAM entity_type {entity_type!r}; "
            f"expected one of {sorted(_VALID_ENTITIES)}"
        )
        raise ValueError(message)
    if store is None:
        store = OamonitorStore.open()
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


__all__ = ["semantic_search"]
