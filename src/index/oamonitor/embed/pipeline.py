"""Embed OAM-CH rows into Qdrant — one Qdrant point per entity (no chunking).

OAM rows have short, single-line ``embedding_text`` (title + identifiers +
acronyms + OA color), so chunking buys us nothing. Each row maps to one
Qdrant point in a per-entity collection so a search ``target`` simply
selects the collection name.

Reuses ``RCPEmbeddingClient`` and ``QdrantStore`` from the openalex index
via duck typing on ``config.rcp.*`` / ``config.qdrant.*`` — the same
pattern zenodo / huggingface / github etc. use.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Any

from src.index._rcp.embed_client import RCPEmbeddingClient
from src.index.openalex.vector.qdrant_store import QdrantStore

if TYPE_CHECKING:
    from src.index.oamonitor.config import OamonitorIndexConfig
    from src.index.oamonitor.storage.duckdb_store import OamonitorStore

LOGGER = logging.getLogger(__name__)

_POINT_NAMESPACE = uuid.NAMESPACE_URL

OAM_COLLECTIONS: dict[str, str] = {
    "journals": "oamonitor_journals",
    "publications": "oamonitor_publications",
    "publishers": "oamonitor_publishers",
    "organisations": "oamonitor_organisations",
}


def qdrant_collection_for(entity: str) -> str:
    try:
        return OAM_COLLECTIONS[entity]
    except KeyError as exc:
        message = (
            f"Unknown OAM entity {entity!r}; expected one of "
            f"{sorted(OAM_COLLECTIONS)}"
        )
        raise ValueError(message) from exc


def _point_id(entity: str, entity_id: str) -> str:
    return str(uuid.uuid5(_POINT_NAMESPACE, f"oamonitor|{entity}|{entity_id}"))


async def _flush_batch(
    *,
    entity: str,
    collection: str,
    batch: list[tuple[str, str]],
    client: RCPEmbeddingClient,
    qdrant: QdrantStore,
    store: OamonitorStore,
    semaphore: asyncio.Semaphore,
    db_lock: asyncio.Lock,
    counter: dict[str, int],
) -> None:
    """Embed + Qdrant-upsert + DuckDB-stamp one batch. Bounded by ``semaphore``.

    The RCP embed call and the Qdrant upsert run concurrently across batches
    (up to ``semaphore`` parallelism). The DuckDB ``mark_embedded`` write is
    serialised via ``db_lock`` because the underlying ``duckdb`` Python
    connection is not thread-safe.
    """
    async with semaphore:
        texts = [text for _, text in batch]
        vectors = await client.embed_all(texts)
        ids = [_point_id(entity, entity_id) for entity_id, _ in batch]
        entity_ids = [entity_id for entity_id, _ in batch]
        payloads = [
            {
                "entity_type": entity,
                "entity_id": entity_id,
                "embedding_text": text,
            }
            for entity_id, text in batch
        ]
        await asyncio.to_thread(
            qdrant.upsert_points,
            collection,
            ids=ids,
            vectors=vectors,
            payloads=payloads,
        )
        async with db_lock:
            store.mark_embedded(entity, entity_ids)
        counter["total"] += len(batch)
        LOGGER.info(
            "oamonitor embed %s: total points pushed=%d", entity, counter["total"],
        )


async def _embed_entity_async(
    *,
    entity: str,
    config: OamonitorIndexConfig,
    store: OamonitorStore,
    limit: int | None,
) -> int:
    """Embed one OAM entity table into its Qdrant collection.

    Batches of ``client.batch_size`` rows are dispatched concurrently up to
    ``config.rcp.max_concurrency`` in-flight calls. The RCP service in
    practice tolerates 4-8 parallel embed requests; each batch is still
    a single HTTP call so we never exceed the documented max-concurrency.
    """
    collection = qdrant_collection_for(entity)
    client = RCPEmbeddingClient(config)  # type: ignore[arg-type]
    qdrant = QdrantStore(config)  # type: ignore[arg-type]
    qdrant.ensure_collection(collection)

    semaphore = asyncio.Semaphore(max(1, int(config.rcp.max_concurrency)))
    db_lock = asyncio.Lock()
    counter: dict[str, int] = {"total": 0}
    tasks: list[asyncio.Task[None]] = []
    pending: list[tuple[str, str]] = []

    def _dispatch(batch: list[tuple[str, str]]) -> None:
        tasks.append(
            asyncio.create_task(
                _flush_batch(
                    entity=entity,
                    collection=collection,
                    batch=batch,
                    client=client,
                    qdrant=qdrant,
                    store=store,
                    semaphore=semaphore,
                    db_lock=db_lock,
                    counter=counter,
                ),
            ),
        )

    seen = 0
    for row in store.iter_rows_for_embedding(entity):
        entity_id = row.get("_id")
        text = row.get("embedding_text")
        if not isinstance(entity_id, str) or not isinstance(text, str) or not text:
            continue
        pending.append((entity_id, text))
        seen += 1
        if len(pending) >= client.batch_size:
            _dispatch(pending)
            pending = []
        if limit is not None and seen >= limit:
            break
    if pending:
        _dispatch(pending)

    if tasks:
        await asyncio.gather(*tasks)

    total = counter["total"]
    LOGGER.info(
        "oamonitor embed %s complete: rows_seen=%d points=%d concurrency=%d",
        entity, seen, total, config.rcp.max_concurrency,
    )
    return total


def embed_entities(
    *,
    config: OamonitorIndexConfig,
    store: OamonitorStore,
    entities: list[str],
    limit: int | None = None,
) -> dict[str, int]:
    """Synchronously embed one or more OAM entity tables. Returns per-entity counts."""
    summary: dict[str, int] = {}
    for entity in entities:
        summary[entity] = asyncio.run(
            _embed_entity_async(
                entity=entity, config=config, store=store, limit=limit,
            ),
        )
    return summary


__all__ = ["OAM_COLLECTIONS", "embed_entities", "qdrant_collection_for"]
