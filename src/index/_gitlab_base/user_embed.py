"""Embed GitLab users into Qdrant via the RCP `/embeddings` endpoint.

Reuses `RCPEmbeddingClient`, `QdrantStore`, and the token chunker from
`src.index.openalex` directly — those modules only access `config.rcp.*`
and `config.qdrant.*` at runtime.

Entry point:

- `embed_users` — chunk + embed un-embedded users (skip rows already
  in `chunks`). The `collection` name is a parameter so each leaf (epfl,
  ethz, datascience) can pass its own Qdrant collection.
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
    from src.index._gitlab_base.user_store import GitLabUserStore

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
    parts: list[str] = [str(row["user_id"])]
    if row.get("name"):
        parts.append(str(row["name"]))
    if row.get("username"):
        parts.append(str(row["username"]))
    if row.get("bio"):
        parts.append(str(row["bio"]))
    if row.get("organization"):
        parts.append(str(row["organization"]))
    if row.get("job_title"):
        parts.append(str(row["job_title"]))
    if row.get("location"):
        parts.append(str(row["location"]))
    text = "\n\n".join(parts)
    if len(text) < min_card_chars:
        return []
    return chunk_text(text, chunk_tokens=chunk_tokens, overlap=overlap)


def _row_to_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "entity_type": "users",
        "entity_id": row["user_id"],
        "user_id": row["user_id"],
        "host": row.get("host"),
        "username": row.get("username"),
    }


async def _embed_users_async(
    *,
    config: Any,
    store: GitLabUserStore,
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
            cid = _chunk_id("users", entity_id, chunk.index)
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
                entity_type="users",
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
    for row in store.stream_rows_for_embedding("users", limit=limit):
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
            pending.append((row["user_id"], base_payload, chunk))
            if len(pending) >= client.batch_size:
                await flush()
    await flush()
    LOGGER.info(
        "embed users complete: rows_seen=%d skipped=%d chunks=%d",
        rows_seen,
        rows_skipped,
        total,
    )
    return total


def embed_users(
    *,
    config: Any,
    store: GitLabUserStore,
    collection: str,
    limit: int | None = None,
) -> dict[str, int]:
    """Synchronously embed GitLab users into the given Qdrant collection."""
    chunks = asyncio.run(
        _embed_users_async(
            config=config,
            store=store,
            collection=collection,
            limit=limit,
        ),
    )
    return {"users": chunks}
