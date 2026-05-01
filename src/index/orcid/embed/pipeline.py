"""Stream DuckDB rows → chunk → embed → upsert into Qdrant.

Idempotent: rows with existing chunks are skipped via
`OrcidDuckDBStore.stream_rows_for_embedding`.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Any

from src.index.orcid.embed.chunker import (
    Chunk,
    chunk_for_affiliation,
    chunk_for_person,
)
from src.index.orcid.embed.rcp_client import RCPEmbeddingClient
from src.index.orcid.vector.qdrant_store import OrcidQdrantStore

if TYPE_CHECKING:
    from src.index.orcid.config import OrcidIndexConfig
    from src.index.orcid.storage.duckdb_store import OrcidDuckDBStore

LOGGER = logging.getLogger(__name__)

_CHUNK_NAMESPACE = uuid.NAMESPACE_URL


def _chunk_id(entity_type: str, entity_id: str, chunk_index: int) -> str:
    return str(
        uuid.uuid5(_CHUNK_NAMESPACE, f"{entity_type}|{entity_id}|{chunk_index}"),
    )


def _person_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "entity_type": "persons",
        "entity_id": row["orcid_id"],
        "orcid_id": row["orcid_id"],
        "display_name": row.get("display_name"),
        "family_name": row.get("family_name"),
    }


def _affiliation_payload(entity_type: str, row: dict[str, Any]) -> dict[str, Any]:
    entity_id = f"{row['orcid_id']}#{row['seq']}"
    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "orcid_id": row["orcid_id"],
        "seq": row["seq"],
        "organization": row.get("organization"),
        "org_ror": row.get("org_ror"),
        "start_date": row.get("start_date"),
        "end_date": row.get("end_date"),
    }


def _row_entity_id(entity_type: str, row: dict[str, Any]) -> str:
    if entity_type == "persons":
        return row["orcid_id"]
    return f"{row['orcid_id']}#{row['seq']}"


def _person_display_name(store: OrcidDuckDBStore, orcid_id: str) -> str | None:
    person = store.fetch_person(orcid_id)
    if not person:
        return None
    return person.get("display_name")


def _build_chunks(
    *,
    entity_type: str,
    row: dict[str, Any],
    store: OrcidDuckDBStore,
    chunk_tokens: int,
    overlap: int,
) -> list[Chunk]:
    if entity_type == "persons":
        employments = store.list_employments(row["orcid_id"])
        affiliations: list[str] = sorted(
            {
                str(org)
                for emp in employments
                if (org := emp.get("organization"))
            },
        )
        return chunk_for_person(
            row,
            affiliations,
            chunk_tokens=chunk_tokens,
            overlap=overlap,
        )
    person_name = _person_display_name(store, row["orcid_id"])
    return chunk_for_affiliation(
        person_name,
        row,
        chunk_tokens=chunk_tokens,
        overlap=overlap,
    )


async def _embed_entity_async(
    *,
    config: OrcidIndexConfig,
    store: OrcidDuckDBStore,
    entity_type: str,
    limit: int | None,
) -> int:
    client = RCPEmbeddingClient(config)
    qdrant = OrcidQdrantStore(config)
    qdrant.ensure_collection(entity_type)

    pending: list[tuple[str, dict[str, Any], Chunk]] = []
    total_chunks = 0

    async def flush() -> None:
        nonlocal total_chunks
        if not pending:
            return
        texts = [c.text for _, _, c in pending]
        vectors = await client.embed_all(texts)
        ids: list[str] = []
        payloads: list[dict[str, Any]] = []
        for entity_id, base_payload, chunk in pending:
            chunk_id = _chunk_id(entity_type, entity_id, chunk.index)
            ids.append(chunk_id)
            payloads.append({**base_payload, "chunk_index": chunk.index})
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
        total_chunks += len(pending)
        pending.clear()

    rows_seen = 0
    for row in store.stream_rows_for_embedding(entity_type, limit=limit):
        rows_seen += 1
        chunks = _build_chunks(
            entity_type=entity_type,
            row=row,
            store=store,
            chunk_tokens=config.chunking.size_tokens,
            overlap=config.chunking.overlap_tokens,
        )
        if not chunks:
            continue
        if entity_type == "persons":
            base_payload = _person_payload(row)
        else:
            base_payload = _affiliation_payload(entity_type, row)
        entity_id = _row_entity_id(entity_type, row)
        for chunk in chunks:
            pending.append((entity_id, base_payload, chunk))
            if len(pending) >= client.batch_size:
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
    config: OrcidIndexConfig,
    store: OrcidDuckDBStore,
    entity_types: list[str],
    limit: int | None = None,
) -> dict[str, int]:
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
