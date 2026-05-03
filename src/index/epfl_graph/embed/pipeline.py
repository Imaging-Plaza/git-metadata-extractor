"""Embed disciplines into a single Qdrant collection.

Each category becomes one point. Embedding text is the concatenation of
``name + canonical Wikipedia title + top anchor concepts`` (built during
ingest). No chunking — disciplines are short by construction (~50-200
tokens), so the single-point shape is both simpler and easier to query.

Reuses ``RCPEmbeddingClient`` and ``QdrantStore`` from the openalex
index — both clients are duck-typed against ``config.rcp.*`` /
``config.qdrant.*``, which :class:`EpflGraphIndexConfig` mirrors.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Any

from src.index.openalex.embed.rcp_client import RCPEmbeddingClient
from src.index.openalex.vector.qdrant_store import QdrantStore

if TYPE_CHECKING:
    from src.index.epfl_graph.config import EpflGraphIndexConfig
    from src.index.epfl_graph.storage.duckdb_store import EpflGraphStore

LOGGER = logging.getLogger(__name__)

EPFL_GRAPH_COLLECTION = "epfl_graph_disciplines"
_POINT_NAMESPACE = uuid.NAMESPACE_URL


def _point_id(category_id: str) -> str:
    return str(uuid.uuid5(_POINT_NAMESPACE, f"epfl_graph|{category_id}"))


def _row_to_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "entity_type": "disciplines",
        "entity_id": row["category_id"],
        "category_id": row["category_id"],
        "name": row.get("name"),
        "depth": row.get("depth"),
        "parent_id": row.get("parent_id"),
        "wikipedia_page_id": row.get("wikipedia_page_id"),
        "wikipedia_url": row.get("wikipedia_url"),
        "graphsearch_url": row.get("graphsearch_url"),
        "n_concepts": row.get("n_concepts"),
        "n_children": row.get("n_children"),
        "embedding_text": row.get("embedding_text"),
    }


async def _embed_async(
    *,
    config: EpflGraphIndexConfig,
    store: EpflGraphStore,
    limit: int | None,
) -> int:
    config.require_rcp()
    client = RCPEmbeddingClient(config)  # type: ignore[arg-type]
    qdrant = QdrantStore(config)  # type: ignore[arg-type]
    qdrant.ensure_collection(EPFL_GRAPH_COLLECTION)

    batch: list[dict[str, Any]] = []
    batch_size = max(1, int(config.rcp.batch_size))
    total = 0

    async def flush() -> None:
        nonlocal total
        if not batch:
            return
        texts = [r["embedding_text"] for r in batch]
        vectors = await client.embed_all(texts)
        ids = [_point_id(r["category_id"]) for r in batch]
        payloads = [_row_to_payload(r) for r in batch]
        qdrant.upsert_points(
            EPFL_GRAPH_COLLECTION,
            ids=ids,
            vectors=vectors,
            payloads=payloads,
        )
        total += len(batch)
        LOGGER.info(
            "epfl_graph: embedded %d / running total %d", len(batch), total,
        )
        batch.clear()

    seen = 0
    for row in store.iter_categories_for_embedding(min_depth=config.filter.min_depth):
        if not row.get("embedding_text"):
            continue
        batch.append(row)
        seen += 1
        if len(batch) >= batch_size:
            await flush()
        if limit is not None and seen >= limit:
            break
    await flush()
    LOGGER.info(
        "epfl_graph: embed complete — %d disciplines pushed to %s",
        total,
        EPFL_GRAPH_COLLECTION,
    )
    return total


def embed_disciplines(
    config: EpflGraphIndexConfig,
    store: EpflGraphStore,
    *,
    limit: int | None = None,
) -> int:
    return asyncio.run(_embed_async(config=config, store=store, limit=limit))
