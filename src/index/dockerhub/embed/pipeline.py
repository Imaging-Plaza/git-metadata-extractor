"""Embed Docker Hub repositories into Qdrant via the RCP `/embeddings` endpoint.

Reuses `RCPEmbeddingClient`, `QdrantStore`, and the token chunker from
`src.index.openalex` / `src.index._rcp` directly — those modules only
access `config.rcp.*` and `config.qdrant.*` at runtime, both of which
`DockerhubIndexConfig` mirrors field-for-field. Same pattern as
`src.index.github_repos`.

`embed_images` chunks + embeds un-embedded image rows (those without
`chunks`). The composite text is `repo_id` + short description +
full_description (README); rows shorter than `min_card_chars` are skipped.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from src.index._rcp.embed_client import RCPEmbeddingClient
from src.index.openalex.embed.chunker import Chunk, chunk_text
from src.index.openalex.vector.qdrant_store import QdrantStore

# Qdrant upsert retry policy: 5s, 15s, 45s, 135s of backoff before giving
# up on a single batch (~3.5min). Matches the other indices.
_QDRANT_RETRY_DELAYS_SECONDS: tuple[int, ...] = (5, 15, 45, 135)

_CHUNK_NAMESPACE = uuid.NAMESPACE_URL

DOCKERHUB_COLLECTION = "dockerhub"

if TYPE_CHECKING:
    from src.index.dockerhub.config import DockerhubIndexConfig
    from src.index.dockerhub.storage.duckdb_store import DockerhubStore

LOGGER = logging.getLogger(__name__)


def _chunk_id(entity_type: str, entity_id: str, chunk_index: int) -> str:
    return str(
        uuid.uuid5(_CHUNK_NAMESPACE, f"{entity_type}|{entity_id}|{chunk_index}"),
    )


def _row_tags(row: dict[str, Any]) -> list[str]:
    raw = row.get("tags")
    if isinstance(raw, str):
        try:
            return [str(x) for x in (json.loads(raw) or [])]
        except json.JSONDecodeError:
            return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    return []


def _row_to_chunks(
    row: dict[str, Any],
    *,
    chunk_tokens: int,
    overlap: int,
    min_card_chars: int,
    full_description_max_bytes: int,
) -> list[Chunk]:
    parts: list[str] = [str(row["repo_id"])]
    if row.get("description"):
        parts.append(str(row["description"]))
    full = row.get("full_description")
    if isinstance(full, str) and full:
        parts.append(full[:full_description_max_bytes])
    text = "\n\n".join(parts)
    if len(text) < min_card_chars:
        return []
    return chunk_text(text, chunk_tokens=chunk_tokens, overlap=overlap)


def _row_to_payload(row: dict[str, Any]) -> dict[str, Any]:
    last_updated = row.get("last_updated")
    return {
        "entity_type": "images",
        "entity_id": row["repo_id"],
        "repo_id": row["repo_id"],
        "namespace": row.get("namespace"),
        "name": row.get("name"),
        "image": f"docker.io/{row['repo_id']}",
        "is_official": row.get("is_official"),
        "star_count": row.get("star_count"),
        "pull_count": row.get("pull_count"),
        "tags": _row_tags(row),
        "last_updated": last_updated.isoformat()
        if hasattr(last_updated, "isoformat")
        else last_updated,
    }


async def _embed_images_async(
    *,
    config: DockerhubIndexConfig,
    store: DockerhubStore,
    limit: int | None,
) -> int:
    client = RCPEmbeddingClient(config)  # type: ignore[arg-type]
    qdrant = QdrantStore(config)  # type: ignore[arg-type]
    qdrant.ensure_collection(DOCKERHUB_COLLECTION)

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
            cid = _chunk_id("images", entity_id, chunk.index)
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
        # Qdrant upsert FIRST, with retry — a crash before the DuckDB mark
        # means a wasted batch on resume, never inconsistency.
        last_exc: Exception | None = None
        for attempt, delay in enumerate((0, *_QDRANT_RETRY_DELAYS_SECONDS)):
            if delay:
                LOGGER.warning(
                    "qdrant upsert retry %d/%d in %ds (last error: %s)",
                    attempt, len(_QDRANT_RETRY_DELAYS_SECONDS), delay, last_exc,
                )
                time.sleep(delay)
            try:
                qdrant.upsert_points(
                    DOCKERHUB_COLLECTION, ids=ids, vectors=vectors, payloads=payloads,
                )
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
        else:
            LOGGER.error(
                "qdrant upsert giving up after %d attempts",
                len(_QDRANT_RETRY_DELAYS_SECONDS),
            )
            raise last_exc  # type: ignore[misc]

        for crow in chunk_rows:
            store.upsert_chunk(
                chunk_id=crow["chunk_id"],
                entity_type="images",
                entity_id=crow["entity_id"],
                chunk_index=crow["chunk_index"],
                text=crow["text"],
                token_count=crow["token_count"],
                vector_id=crow["chunk_id"],
            )
        total += len(pending)
        pending.clear()

    dh = config.dockerhub
    for row in store.stream_rows_for_embedding("images", limit=limit):
        chunks = _row_to_chunks(
            row,
            chunk_tokens=config.chunking.size_tokens,
            overlap=config.chunking.overlap_tokens,
            min_card_chars=dh.min_card_chars,
            full_description_max_bytes=dh.full_description_max_bytes,
        )
        if not chunks:
            continue
        base_payload = _row_to_payload(row)
        entity_id = str(row["repo_id"])
        for chunk in chunks:
            pending.append((entity_id, base_payload, chunk))
            if len(pending) >= client.batch_size:
                await flush()
    await flush()
    LOGGER.info("embed images complete: chunks=%d", total)
    return total


def embed_images(
    *,
    config: DockerhubIndexConfig,
    store: DockerhubStore,
    limit: int | None = None,
) -> dict[str, int]:
    """Synchronously embed un-embedded Docker Hub images into Qdrant."""
    chunks = asyncio.run(
        _embed_images_async(config=config, store=store, limit=limit),
    )
    return {"images": chunks}
