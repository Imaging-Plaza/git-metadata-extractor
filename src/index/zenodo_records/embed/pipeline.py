"""Embed Zenodo records into Qdrant via the RCP `/embeddings` endpoint.

Reuses `RCPEmbeddingClient`, `QdrantStore`, and the token chunker from
`src.index.openalex` directly — those modules only access `config.rcp.*` and
`config.qdrant.*` at runtime, both of which `ZenodoIndexConfig` mirrors
field-for-field.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Any

from src.index.openalex.embed.chunker import Chunk, chunk_text
from src.index._rcp.embed_client import RCPEmbeddingClient
from src.index.openalex.vector.qdrant_store import QdrantStore

if TYPE_CHECKING:
    from src.index.zenodo_records.config import ZenodoIndexConfig
    from src.index.zenodo_records.storage.duckdb_store import ZenodoRecordsStore

LOGGER = logging.getLogger(__name__)

_CHUNK_NAMESPACE = uuid.NAMESPACE_URL

ZENODO_COLLECTION = "zenodo_records"


def _chunk_id(entity_type: str, entity_id: str, chunk_index: int) -> str:
    return str(
        uuid.uuid5(_CHUNK_NAMESPACE, f"{entity_type}|{entity_id}|{chunk_index}"),
    )


def _row_to_chunks(
    row: dict[str, Any],
    *,
    chunk_tokens: int,
    overlap: int,
) -> list[Chunk]:
    parts = [p for p in (row.get("title"), row.get("description")) if p]
    if not parts:
        return []
    text = "\n\n".join(parts)
    return chunk_text(text, chunk_tokens=chunk_tokens, overlap=overlap)


def _row_to_payload(row: dict[str, Any]) -> dict[str, Any]:
    pub_date = row.get("publication_date")
    return {
        "entity_type": "records",
        "entity_id": row["zenodo_id"],
        "zenodo_id": row["zenodo_id"],
        "title": row.get("title"),
        "doi": row.get("doi"),
        "year": pub_date.year if pub_date else None,
        "resource_type": row.get("resource_type"),
        "access_right": row.get("access_right"),
    }


async def _embed_records_async(
    *,
    config: ZenodoIndexConfig,
    store: ZenodoRecordsStore,
    limit: int | None,
) -> int:
    # The openalex RCP/Qdrant clients are duck-typed against `config.rcp.*`
    # and `config.qdrant.*`, both of which `ZenodoIndexConfig` mirrors. The
    # mypy noise from the openalex-specific TYPE_CHECKING annotation is
    # silenced explicitly at each reuse site.
    client = RCPEmbeddingClient(config)  # type: ignore[arg-type]
    qdrant = QdrantStore(config)  # type: ignore[arg-type]
    qdrant.ensure_collection(ZENODO_COLLECTION)

    pending: list[tuple[str, dict[str, Any], Chunk]] = []
    total = 0

    async def flush() -> None:
        nonlocal total
        if not pending:
            return
        texts = [c.text for _, _, c in pending]
        vectors = await client.embed_all(texts)
        ids: list[str] = []
        payloads: list[dict[str, Any]] = []
        for entity_id, base_payload, chunk in pending:
            cid = _chunk_id("records", entity_id, chunk.index)
            ids.append(cid)
            payloads.append({**base_payload, "chunk_index": chunk.index})
            store.upsert_chunk(
                chunk_id=cid,
                entity_type="records",
                entity_id=entity_id,
                chunk_index=chunk.index,
                text=chunk.text,
                token_count=chunk.token_count,
                vector_id=cid,
            )
        qdrant.upsert_points(
            ZENODO_COLLECTION,
            ids=ids,
            vectors=vectors,
            payloads=payloads,
        )
        total += len(pending)
        pending.clear()

    rows_seen = 0
    for row in store.stream_rows_for_embedding("records", limit=limit):
        rows_seen += 1
        chunks = _row_to_chunks(
            row,
            chunk_tokens=config.chunking.size_tokens,
            overlap=config.chunking.overlap_tokens,
        )
        if not chunks:
            continue
        base_payload = _row_to_payload(row)
        for chunk in chunks:
            pending.append((row["zenodo_id"], base_payload, chunk))
            if len(pending) >= client.batch_size:
                await flush()
    await flush()
    LOGGER.info(
        "embed records complete: rows_seen=%d chunks=%d",
        rows_seen,
        total,
    )
    return total


def embed_records(
    *,
    config: ZenodoIndexConfig,
    store: ZenodoRecordsStore,
    limit: int | None = None,
) -> dict[str, int]:
    """Synchronously embed Zenodo records (only entity type today)."""
    chunks = asyncio.run(
        _embed_records_async(config=config, store=store, limit=limit),
    )
    return {"records": chunks}
