"""Semantic retrieval over the Zenodo index.

Embed → Qdrant search → RCP rerank → DuckDB hydrate (records + creators +
communities). Reuses the openalex RCP + Qdrant clients directly.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from src.index.openalex.embed.rcp_client import RCPEmbeddingClient
from src.index.openalex.rerank.rcp_client import RCPRerankerClient
from src.index.openalex.vector.qdrant_store import QdrantStore
from src.index.zenodo_records.embed.pipeline import ZENODO_COLLECTION
from src.index.zenodo_records.storage.duckdb_store import ZenodoRecordsStore

if TYPE_CHECKING:
    from src.index.zenodo_records.config import ZenodoIndexConfig

LOGGER = logging.getLogger(__name__)


def _payload_to_doc(payload: dict[str, Any]) -> str:
    title = payload.get("title")
    if title:
        return str(title)
    return json.dumps(payload, ensure_ascii=False)


def _hydrate(store: ZenodoRecordsStore, zenodo_id: str) -> dict[str, Any] | None:
    record = store.fetch_record(zenodo_id)
    if record is None:
        return None
    creators = store.connect().execute(
        "SELECT c.creator_key, c.display_name, c.orcid, c.affiliation, rc.position "
        "FROM record_creators rc JOIN creators c "
        "  ON c.creator_key = rc.creator_key "
        "WHERE rc.record_id = ? "
        "ORDER BY rc.position",
        [zenodo_id],
    ).fetchall()
    record["creators"] = [
        {
            "creator_key": r[0],
            "display_name": r[1],
            "orcid": r[2],
            "affiliation": r[3],
            "position": r[4],
        }
        for r in creators
    ]
    community_ids = store.connect().execute(
        "SELECT community_id FROM record_communities WHERE record_id = ?",
        [zenodo_id],
    ).fetchall()
    record["communities"] = [r[0] for r in community_ids]
    return record


async def _async_search(
    *,
    config: ZenodoIndexConfig,
    query: str,
    top_k: int,
    candidate_k: int,
    filter_payload: dict[str, Any] | None,
    store: ZenodoRecordsStore,
) -> list[dict[str, Any]]:
    # See note in src/index/zenodo/embed/pipeline.py: clients are
    # duck-typed against the openalex config shape, which Zenodo mirrors.
    embed = RCPEmbeddingClient(config)  # type: ignore[arg-type]
    qdrant = QdrantStore(config)  # type: ignore[arg-type]
    rerank = RCPRerankerClient(config)  # type: ignore[arg-type]

    [query_vec] = await embed.embed_all([query])
    candidates = qdrant.search(
        ZENODO_COLLECTION,
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
        entity_id = payload.get("entity_id") or payload.get("zenodo_id")
        if not entity_id:
            continue
        hydrated.append(
            {
                "id": hit["id"],
                "vector_score": hit["score"],
                "rerank_score": hit.get("rerank_score"),
                "payload": payload,
                "entity": _hydrate(store, str(entity_id)),
            },
        )
    return hydrated


def semantic_search(
    *,
    config: ZenodoIndexConfig,
    query: str,
    top_k: int = 10,
    candidate_k: int = 50,
    filter_payload: dict[str, Any] | None = None,
    store: ZenodoRecordsStore | None = None,
) -> list[dict[str, Any]]:
    if store is None:
        store = ZenodoRecordsStore.open()
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
