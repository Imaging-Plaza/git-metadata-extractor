"""Shared embed flush loop for the github user/org account indices.

Each concrete index passes:

  - the DuckDB table name + id column (to stream un-embedded rows)
  - the `entity_type` string written into the `chunks` table
  - the Qdrant collection to upsert into
  - two callbacks: one to compose embedding text from a row, one to
    build the Qdrant payload from a row

…and gets the standard chunk → embed → Qdrant-upsert → DuckDB-mark
flow back, with the same retry policy the repo index uses.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import TYPE_CHECKING, Any, Callable

from src.index._github_accounts_base.storage_base import (
    stream_unembedded,
    upsert_chunk,
)
from src.index.openalex.embed.chunker import Chunk, chunk_text
from src.index.openalex.embed.rcp_client import RCPEmbeddingClient
from src.index.openalex.vector.qdrant_store import QdrantStore

if TYPE_CHECKING:
    from src.index._github_accounts_base.config_base import AccountIndexConfigBase
    import duckdb

LOGGER = logging.getLogger(__name__)

# Matches the repo index policy: 5s, 15s, 45s, 135s of backoff before
# giving up on a single Qdrant batch. ~3.5min total budget.
_QDRANT_RETRY_DELAYS_SECONDS: tuple[int, ...] = (5, 15, 45, 135)

_CHUNK_NAMESPACE = uuid.NAMESPACE_URL


def _chunk_id(entity_type: str, entity_id: str, chunk_index: int) -> str:
    return str(
        uuid.uuid5(_CHUNK_NAMESPACE, f"{entity_type}|{entity_id}|{chunk_index}"),
    )


async def embed_accounts_async(
    *,
    config: AccountIndexConfigBase,
    conn: duckdb.DuckDBPyConnection,
    table: str,
    id_column: str,
    entity_type: str,
    collection: str,
    compose_text: Callable[[dict[str, Any]], str],
    build_payload: Callable[[dict[str, Any]], dict[str, Any]],
    limit: int | None = None,
) -> int:
    """Embed un-embedded rows from `<table>` into `<collection>`.

    The two callbacks are the only per-kind behaviour:
      - `compose_text(row) -> str`  builds the text fed to the embedder.
        Returning an empty / very short string causes the row to be
        skipped (min_card_chars from config).
      - `build_payload(row) -> dict`  becomes the Qdrant point payload
        before the per-chunk index is stamped on.

    Returns the total number of chunks pushed.
    """
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
            cid = _chunk_id(entity_type, entity_id, chunk.index)
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

        # Qdrant first, with retry — same ordering rationale as the repo
        # index: a Qdrant timeout before the DuckDB mark means a wasted
        # batch on resume, never inconsistency.
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
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
        else:
            LOGGER.error(
                "qdrant upsert giving up after %d attempts",
                len(_QDRANT_RETRY_DELAYS_SECONDS),
            )
            raise last_exc  # type: ignore[misc]

        for row in chunk_rows:
            upsert_chunk(
                conn,
                chunk_id=row["chunk_id"],
                entity_type=entity_type,
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
    min_card_chars = config.github.min_card_chars
    for row in stream_unembedded(
        conn,
        table=table,
        id_column=id_column,
        entity_type=entity_type,
        limit=limit,
    ):
        rows_seen += 1
        text = compose_text(row)
        if not text or len(text) < min_card_chars:
            rows_skipped += 1
            continue
        chunks = chunk_text(
            text,
            chunk_tokens=config.chunking.size_tokens,
            overlap=config.chunking.overlap_tokens,
        )
        if not chunks:
            rows_skipped += 1
            continue
        base_payload = build_payload(row)
        entity_id = str(row[id_column])
        for chunk in chunks:
            pending.append((entity_id, base_payload, chunk))
            if len(pending) >= client.batch_size:
                await flush()
    await flush()
    LOGGER.info(
        "embed %s complete: rows_seen=%d skipped=%d chunks=%d",
        entity_type,
        rows_seen,
        rows_skipped,
        total,
    )
    return total
