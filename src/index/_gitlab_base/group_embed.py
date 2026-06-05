"""Embed GitLab groups into Qdrant via the RCP `/embeddings` endpoint.

Reuses `RCPEmbeddingClient`, `QdrantStore`, and the token chunker from
`src.index.openalex` directly — those modules only access `config.rcp.*`
and `config.qdrant.*` at runtime.

Entry point:

- `embed_groups` — chunk + embed un-embedded groups (skip rows already
  in `chunks`). The `collection` name is a parameter so each leaf (epfl,
  ethz, datascience, com) can pass its own Qdrant collection.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from src.index._rcp.embed_client import RCPEmbeddingClient
from src.index.openalex.embed.chunker import Chunk, chunk_text
from src.index.openalex.vector.qdrant_store import QdrantStore

# Qdrant upsert retry policy. Exponential backoff: 5s, 15s, 45s, 135s —
# total ~3.5 min before giving up on a single batch.
_QDRANT_RETRY_DELAYS_SECONDS: tuple[int, ...] = (5, 15, 45, 135)

if TYPE_CHECKING:
    from src.index._gitlab_base.group_store import GitLabGroupStore

LOGGER = logging.getLogger(__name__)

_CHUNK_NAMESPACE = uuid.NAMESPACE_URL


def _chunk_id(entity_type: str, entity_id: str, chunk_index: int) -> str:
    return str(
        uuid.uuid5(_CHUNK_NAMESPACE, f"{entity_type}|{entity_id}|{chunk_index}"),
    )


def _row_to_chunks(
    row: dict[str, Any],
    *,
    chunk_tokens: int,
    overlap: int,
    min_card_chars: int,
) -> list[Chunk]:
    parts: list[str] = [str(row["group_id"])]
    if row.get("name"):
        parts.append(str(row["name"]))
    if row.get("description"):
        parts.append(str(row["description"]))
    text = "\n\n".join(parts)
    if len(text) < min_card_chars:
        return []
    return chunk_text(text, chunk_tokens=chunk_tokens, overlap=overlap)


def _row_to_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "entity_type": "groups",
        "entity_id": row["group_id"],
        "group_id": row["group_id"],
        "host": row.get("host"),
        "full_path": row.get("full_path"),
        "visibility": row.get("visibility"),
    }


async def _embed_groups_async(
    *,
    config: Any,
    store: GitLabGroupStore,
    collection: str,
    limit: int | None,
) -> int:
    client = RCPEmbeddingClient(config)  # type: ignore[arg-type]
    qdrant = QdrantStore(config)  # type: ignore[arg-type]
    qdrant.ensure_collection(collection)

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
        chunk_rows: list[dict[str, Any]] = []
        for entity_id, base_payload, chunk in pending:
            cid = _chunk_id("groups", entity_id, chunk.index)
            ids.append(cid)
            payloads.append({**base_payload, "chunk_index": chunk.index})
            chunk_rows.append(
                {
                    "chunk_id": cid,
                    "entity_id": entity_id,
                    "chunk_index": chunk.index,
                    "text": chunk.text,
                    "token_count": chunk.token_count,
                },
            )
        # Qdrant upsert FIRST, with retry. If we wrote chunks to DuckDB before
        # this, a Qdrant timeout would leave orphan rows that block re-embed
        # on the same batch. Order is: vectors land in Qdrant, then DuckDB
        # records "this batch is embedded" — a crash between the two means
        # a wasted batch (re-embedded on resume) but no inconsistency.
        last_exc: Exception | None = None
        for attempt, delay in enumerate((0, *_QDRANT_RETRY_DELAYS_SECONDS)):
            if delay:
                LOGGER.warning(
                    "qdrant upsert retry %d/%d in %ds (last error: %s)",
                    attempt,
                    len(_QDRANT_RETRY_DELAYS_SECONDS),
                    delay,
                    last_exc,
                )
                time.sleep(delay)
            try:
                qdrant.upsert_points(
                    collection,
                    ids=ids,
                    vectors=vectors,
                    payloads=payloads,
                )
                break
            except Exception as exc:  # noqa: BLE001 — qdrant_client raises a wide variety
                last_exc = exc
        else:
            LOGGER.error("qdrant upsert giving up after %d attempts", len(_QDRANT_RETRY_DELAYS_SECONDS))
            raise last_exc  # type: ignore[misc]
        for row in chunk_rows:
            store.upsert_chunk(
                chunk_id=row["chunk_id"],
                entity_type="groups",
                entity_id=row["entity_id"],
                chunk_index=row["chunk_index"],
                text=row["text"],
                token_count=row["token_count"],
                vector_id=row["chunk_id"],
            )
        total += len(pending)
        pending.clear()

    rows_seen = 0
    rows_skipped = 0
    for row in store.stream_rows_for_embedding("groups", limit=limit):
        rows_seen += 1
        chunks = _row_to_chunks(
            row,
            chunk_tokens=config.chunking.size_tokens,
            overlap=config.chunking.overlap_tokens,
            min_card_chars=config.gitlab.min_card_chars,
        )
        if not chunks:
            rows_skipped += 1
            continue
        base_payload = _row_to_payload(row)
        for chunk in chunks:
            pending.append((row["group_id"], base_payload, chunk))
            if len(pending) >= client.batch_size:
                await flush()
    await flush()
    LOGGER.info(
        "embed groups complete: rows_seen=%d skipped=%d chunks=%d",
        rows_seen,
        rows_skipped,
        total,
    )
    return total


def embed_groups(
    *,
    config: Any,
    store: GitLabGroupStore,
    collection: str,
    limit: int | None = None,
) -> dict[str, int]:
    """Synchronously embed GitLab groups into the given Qdrant collection."""
    chunks = asyncio.run(
        _embed_groups_async(
            config=config,
            store=store,
            collection=collection,
            limit=limit,
        ),
    )
    return {"groups": chunks}
