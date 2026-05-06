"""Semantic retrieval over the RenkuLab index.

Embed → Qdrant search (across one or all entity collections) → RCP
rerank → DuckDB hydrate. Reuses the openalex RCP + Qdrant clients
directly; same duck-typed config trick as zenodo.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from src.index.openalex.embed.rcp_client import RCPEmbeddingClient
from src.index.openalex.rerank.rcp_client import RCPRerankerClient
from src.index.openalex.vector.qdrant_store import QdrantStore
from src.index.renkulab.embed.pipeline import COLLECTION_BY_ENTITY
from src.index.renkulab.storage.duckdb_store import RenkulabStore

if TYPE_CHECKING:
    from src.index.renkulab.config import RenkulabIndexConfig

LOGGER = logging.getLogger(__name__)


def _payload_to_doc(payload: dict[str, Any]) -> str:
    for key in ("name", "first_name", "slug", "path"):
        v = payload.get(key)
        if v:
            return str(v)
    return json.dumps(payload, ensure_ascii=False)


def _hydrate(
    store: RenkulabStore,
    entity_type: str,
    entity_id: str,
) -> dict[str, Any] | None:
    return store.fetch_entity(entity_type, entity_id)


def _resolve_collections(entity_types: list[str] | None) -> list[tuple[str, str]]:
    if not entity_types:
        return [(et, COLLECTION_BY_ENTITY[et]) for et in COLLECTION_BY_ENTITY]
    out: list[tuple[str, str]] = []
    for et in entity_types:
        if et not in COLLECTION_BY_ENTITY:
            message = (
                f"Unknown entity_type: {et!r}. "
                f"Known: {sorted(COLLECTION_BY_ENTITY)}"
            )
            raise ValueError(message)
        out.append((et, COLLECTION_BY_ENTITY[et]))
    return out


async def _async_search(
    *,
    config: RenkulabIndexConfig,
    query: str,
    entity_types: list[str] | None,
    top_k: int,
    candidate_k: int,
    filter_payload: dict[str, Any] | None,
    store: RenkulabStore,
) -> list[dict[str, Any]]:
    embed = RCPEmbeddingClient(config)  # type: ignore[arg-type]
    qdrant = QdrantStore(config)  # type: ignore[arg-type]
    rerank = RCPRerankerClient(config)  # type: ignore[arg-type]

    [query_vec] = await embed.embed_all([query])

    collections = _resolve_collections(entity_types)
    candidates: list[dict[str, Any]] = []
    for entity_type, collection in collections:
        try:
            hits = qdrant.search(
                collection,
                query_vector=query_vec,
                top_k=candidate_k,
                filter_payload=filter_payload,
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("search %s failed: %s", collection, exc)
            continue
        for hit in hits:
            payload = dict(hit.get("payload") or {})
            payload.setdefault("entity_type", entity_type)
            candidates.append({**hit, "payload": payload})

    if not candidates:
        return []

    candidates.sort(key=lambda h: h.get("score", 0.0), reverse=True)
    candidates = candidates[: max(candidate_k, top_k)]

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
        if not entity_type or not entity_id:
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
    config: RenkulabIndexConfig,
    query: str,
    entity_types: list[str] | None = None,
    top_k: int = 10,
    candidate_k: int = 50,
    filter_payload: dict[str, Any] | None = None,
    store: RenkulabStore | None = None,
) -> list[dict[str, Any]]:
    if store is None:
        store = RenkulabStore.open()
    return asyncio.run(
        _async_search(
            config=config,
            query=query,
            entity_types=entity_types,
            top_k=top_k,
            candidate_k=candidate_k,
            filter_payload=filter_payload,
            store=store,
        ),
    )
