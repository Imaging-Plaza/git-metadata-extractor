"""End-to-end semantic retrieval: embed → vector search → rerank → hydrate."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from src.index.huggingface.embed.rcp_client import RCPEmbeddingClient
from src.index.huggingface.models import ENTITY_TYPE_SINGULAR
from src.index.huggingface.rerank.rcp_client import RCPRerankerClient
from src.index.huggingface.storage.duckdb_store import DuckDBStore
from src.index.huggingface.vector.qdrant_store import COLLECTION_FOR_TABLE, QdrantStore

if TYPE_CHECKING:
    from src.index.huggingface.config import HuggingFaceIndexConfig

LOGGER = logging.getLogger(__name__)


async def _async_search(
    *,
    config: HuggingFaceIndexConfig,
    query: str,
    entity_table: str,
    top_k: int,
    candidate_k: int,
    filter_payload: dict[str, Any] | None,
    store: DuckDBStore,
) -> list[dict[str, Any]]:
    if entity_table not in COLLECTION_FOR_TABLE:
        message = f"Unknown entity table: {entity_table}"
        raise ValueError(message)
    embed = RCPEmbeddingClient(config)
    qdrant = QdrantStore(config)
    rerank = RCPRerankerClient(config)
    collection = COLLECTION_FOR_TABLE[entity_table]

    instruction = config.rcp.query_instruction
    formatted_query = (
        f"Instruct: {instruction}\nQuery: {query}" if instruction else query
    )
    [query_vec] = await embed.embed_all([formatted_query])
    candidates = qdrant.search(
        collection,
        query_vector=query_vec,
        top_k=candidate_k,
        filter_payload=filter_payload,
    )
    if not candidates:
        return []

    docs = [_payload_to_doc(c["payload"]) for c in candidates]
    # Rerank the full candidate pool so per-entity dedup below has enough
    # ranked chunks to fill `top_k` distinct entities.
    reranked = await rerank.rerank(query, docs, top_n=len(candidates))
    if not reranked:
        ordered = list(candidates)
    else:
        ordered = []
        for r in reranked:
            cand = candidates[r["index"]]
            ordered.append({**cand, "rerank_score": r["relevance_score"]})

    # Dedup at the entity level — Qdrant points are chunks, but a search
    # result should return one row per repo/namespace (the best chunk of it).
    hydrated: list[dict[str, Any]] = []
    seen_repo_ids: set[str] = set()
    for hit in ordered:
        payload = hit["payload"] or {}
        repo_id = payload.get("repo_id")
        if not repo_id or repo_id in seen_repo_ids:
            continue
        seen_repo_ids.add(repo_id)
        if entity_table == "orgs":
            row = store.fetch_org(repo_id)
        else:
            row = store.fetch_repo(entity_table, repo_id)
        hydrated.append(
            {
                "id": hit["id"],
                "vector_score": hit["score"],
                "rerank_score": hit.get("rerank_score"),
                "payload": payload,
                "entity": row,
            },
        )
        if len(hydrated) >= top_k:
            break
    return hydrated


def _payload_to_doc(payload: dict[str, Any]) -> str:
    repo_id = payload.get("repo_id")
    pipeline_tag = payload.get("pipeline_tag")
    sdk = payload.get("sdk")
    fullname = payload.get("fullname")
    parts = [str(p) for p in (repo_id, fullname, pipeline_tag, sdk) if p]
    return " — ".join(parts) if parts else json.dumps(payload, ensure_ascii=False)


def semantic_search(
    *,
    config: HuggingFaceIndexConfig,
    query: str,
    entity_type: str = "models",
    top_k: int = 10,
    candidate_k: int = 50,
    filter_payload: dict[str, Any] | None = None,
    store: DuckDBStore | None = None,
) -> list[dict[str, Any]]:
    """Synchronous entrypoint used by the CLI and the FastAPI app.

    `entity_type` accepts either the plural table name (`models`) or the
    singular form (`model`) for ergonomics.
    """
    table = entity_type if entity_type in COLLECTION_FOR_TABLE else _table_from_singular(entity_type)
    if store is None:
        store = DuckDBStore.open()
    return asyncio.run(
        _async_search(
            config=config,
            query=query,
            entity_table=table,
            top_k=top_k,
            candidate_k=candidate_k,
            filter_payload=filter_payload,
            store=store,
        ),
    )


def _table_from_singular(value: str) -> str:
    for table, singular in ENTITY_TYPE_SINGULAR.items():
        if singular == value:
            return table
    message = f"Unknown entity type: {value}"
    raise ValueError(message)
