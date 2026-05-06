"""Stream DuckDB rows → chunk → embed → upsert into Qdrant.

Idempotent: rows with existing chunks are skipped via
`DuckDBStore.stream_rows_for_embedding`.

Rebuild path: `rebuild_qdrant_from_chunks` re-derives Qdrant points from the
existing `chunks` table (re-embedding `chunks.text` via RCP) without touching
DuckDB. Used after a Qdrant wipe — same RCP cost as a fresh embed, but the
DuckDB chunks/text stay intact.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Any

_CHUNK_NAMESPACE = uuid.NAMESPACE_URL

ENTITY_TABLES: tuple[str, ...] = (
    "works",
    "authors",
    "institutions",
    "sources",
    "topics",
    "concepts",
)


def _chunk_id(entity_type: str, entity_id: str, chunk_index: int) -> str:
    return str(
        uuid.uuid5(_CHUNK_NAMESPACE, f"{entity_type}|{entity_id}|{chunk_index}"),
    )

from src.index.openalex.embed.chunker import (
    Chunk,
    chunk_for_simple_entity,
    chunk_for_work,
)
from src.index.openalex.embed.rcp_client import RCPEmbeddingClient
from src.index.openalex.vector.qdrant_store import QdrantStore

if TYPE_CHECKING:
    from src.index.openalex.config import OpenAlexIndexConfig
    from src.index.openalex.storage.duckdb_store import DuckDBStore

LOGGER = logging.getLogger(__name__)


def _row_to_chunks(
    entity_type: str,
    row: dict[str, Any],
    *,
    chunk_tokens: int,
    overlap: int,
) -> list[Chunk]:
    if entity_type == "works":
        return chunk_for_work(
            row.get("title"),
            row.get("abstract"),
            chunk_tokens=chunk_tokens,
            overlap=overlap,
        )
    return chunk_for_simple_entity(
        row.get("display_name"),
        chunk_tokens=chunk_tokens,
        overlap=overlap,
    )


def _row_to_payload(entity_type: str, row: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "entity_type": entity_type,
        "entity_id": row["openalex_id"],
        "openalex_id": row["openalex_id"],
    }
    if entity_type == "works":
        payload["title"] = row.get("title")
        payload["year"] = row.get("publication_year")
    else:
        payload["display_name"] = row.get("display_name")
    return payload


async def _embed_entity_async(
    *,
    config: OpenAlexIndexConfig,
    store: DuckDBStore,
    entity_type: str,
    limit: int | None,
) -> int:
    client = RCPEmbeddingClient(config)
    qdrant = QdrantStore(config)
    qdrant.ensure_collection(entity_type)

    pending_chunks: list[tuple[str, dict[str, Any], Chunk]] = []
    total_chunks = 0

    async def flush() -> None:
        nonlocal total_chunks
        if not pending_chunks:
            return
        texts = [c.text for _, _, c in pending_chunks]
        vectors = await client.embed_all(texts)
        ids: list[str] = []
        payloads: list[dict[str, Any]] = []
        for entity_id, base_payload, chunk in pending_chunks:
            chunk_id = _chunk_id(entity_type, entity_id, chunk.index)
            ids.append(chunk_id)
            payload = {**base_payload, "chunk_index": chunk.index}
            payloads.append(payload)
            store.upsert_chunk(
                chunk_id=chunk_id,
                entity_type=entity_type,
                entity_id=entity_id,
                chunk_index=chunk.index,
                text=chunk.text,
                token_count=chunk.token_count,
                vector_id=chunk_id,
            )
        qdrant.upsert_points(
            entity_type,
            ids=ids,
            vectors=vectors,
            payloads=payloads,
        )
        total_chunks += len(pending_chunks)
        pending_chunks.clear()

    rows_seen = 0
    for row in store.stream_rows_for_embedding(entity_type, limit=limit):
        rows_seen += 1
        chunks = _row_to_chunks(
            entity_type,
            row,
            chunk_tokens=config.chunking.size_tokens,
            overlap=config.chunking.overlap_tokens,
        )
        if not chunks:
            continue
        base_payload = _row_to_payload(entity_type, row)
        for chunk in chunks:
            pending_chunks.append((row["openalex_id"], base_payload, chunk))
            if len(pending_chunks) >= client.batch_size:
                await flush()
    await flush()
    LOGGER.info(
        "embed %s complete: rows_seen=%d chunks=%d",
        entity_type,
        rows_seen,
        total_chunks,
    )
    return total_chunks


def embed_entities(
    *,
    config: OpenAlexIndexConfig,
    store: DuckDBStore,
    entity_types: list[str],
    limit: int | None = None,
) -> dict[str, int]:
    """Synchronously embed across the requested entity types."""
    summary: dict[str, int] = {}
    for entity_type in entity_types:
        summary[entity_type] = asyncio.run(
            _embed_entity_async(
                config=config,
                store=store,
                entity_type=entity_type,
                limit=limit,
            ),
        )
    return summary


def _rebuild_select_sql(entity_type: str) -> str:
    """SQL to stream `chunks` rows joined with their source entity.

    Returned columns are stable across entity types so the caller can build a
    payload without per-type branching beyond the `_row_to_payload` shape.
    """
    if entity_type == "works":
        extra = "w.title AS title, w.publication_year AS publication_year, NULL AS display_name"
    else:
        extra = "NULL AS title, NULL AS publication_year, t.display_name AS display_name"
    join_alias = "w" if entity_type == "works" else "t"
    return (  # noqa: S608 - entity_type guarded by ENTITY_TABLES
        "SELECT c.chunk_id, c.entity_id, c.chunk_index, c.text, "
        f"{extra} "
        f"FROM chunks c JOIN {entity_type} {join_alias} "
        f"ON {join_alias}.openalex_id = c.entity_id "
        "WHERE c.entity_type = ? "
        "ORDER BY c.entity_id, c.chunk_index"
    )


def _rebuild_payload(entity_type: str, row: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "entity_type": entity_type,
        "entity_id": row["entity_id"],
        "openalex_id": row["entity_id"],
        "chunk_index": row["chunk_index"],
    }
    if entity_type == "works":
        payload["title"] = row.get("title")
        payload["year"] = row.get("publication_year")
    else:
        payload["display_name"] = row.get("display_name")
    return payload


async def _rebuild_entity_async(
    *,
    config: OpenAlexIndexConfig,
    store: DuckDBStore,
    entity_type: str,
    batch_size: int | None = None,
) -> int:
    """Re-push all `chunks` rows for `entity_type` into Qdrant."""
    if entity_type not in ENTITY_TABLES:
        message = f"Unknown entity_type: {entity_type}"
        raise ValueError(message)

    client = RCPEmbeddingClient(config, batch_size=batch_size)
    qdrant = QdrantStore(config)
    qdrant.ensure_collection(entity_type)

    cur = store.connect().execute(_rebuild_select_sql(entity_type), [entity_type])
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]

    if not rows:
        LOGGER.info("rebuild %s: no chunks to push", entity_type)
        return 0

    pushed = 0
    flush_size = client.batch_size
    for start in range(0, len(rows), flush_size):
        batch = rows[start : start + flush_size]
        texts = [r["text"] for r in batch]
        vectors = await client.embed_all(texts)
        ids = [r["chunk_id"] for r in batch]
        payloads = [_rebuild_payload(entity_type, r) for r in batch]
        qdrant.upsert_points(
            entity_type,
            ids=ids,
            vectors=vectors,
            payloads=payloads,
        )
        pushed += len(batch)
        LOGGER.info(
            "rebuild %s: pushed %d/%d points",
            entity_type,
            pushed,
            len(rows),
        )
    return pushed


def rebuild_qdrant_from_chunks(
    *,
    config: OpenAlexIndexConfig,
    store: DuckDBStore,
    entity_types: list[str],
    batch_size: int | None = None,
) -> dict[str, int]:
    """Rebuild Qdrant collections from the existing DuckDB `chunks` table.

    Re-embeds `chunks.text` via RCP and upserts to Qdrant using the existing
    `chunk_id` as the point id, so the DuckDB ↔ Qdrant link is preserved.
    Used after a Qdrant wipe — does NOT modify DuckDB.
    """
    summary: dict[str, int] = {}
    for entity_type in entity_types:
        summary[entity_type] = asyncio.run(
            _rebuild_entity_async(
                config=config,
                store=store,
                entity_type=entity_type,
                batch_size=batch_size,
            ),
        )
    return summary
