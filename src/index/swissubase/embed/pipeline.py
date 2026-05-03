"""Embed SWISSUbase entities into Qdrant via the RCP `/embeddings` endpoint.

A single Qdrant collection (``swissubase_entities``) holds chunks for
all four entity types (``studies``, ``datasets``, ``persons``,
``institutions``) — the ``entity_type`` payload field disambiguates.
This mirrors the HuggingFace index pattern rather than zenodo's single-
type collection because the user explicitly wants persons / projects
/ resources / authors all reachable via one tool.

Reuses ``RCPEmbeddingClient``, ``QdrantStore``, and the token chunker
from ``src.index.openalex`` — those modules only access ``config.rcp.*``
and ``config.qdrant.*``, both of which ``SwissubaseIndexConfig`` mirrors.

Studies are gated on ``affiliation_match=TRUE`` at the SQL level, so
only the in-scope subset (epfl_sdsc_ethz by default) gets embedded.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Any

from src.index.openalex.embed.chunker import Chunk, chunk_text
from src.index.openalex.embed.rcp_client import RCPEmbeddingClient
from src.index.openalex.vector.qdrant_store import QdrantStore
from src.index.swissubase.storage.duckdb_store import EMBEDDABLE_ENTITY_TYPES

if TYPE_CHECKING:
    from src.index.swissubase.config import SwissubaseIndexConfig
    from src.index.swissubase.storage.duckdb_store import SwissubaseStore

LOGGER = logging.getLogger(__name__)

_CHUNK_NAMESPACE = uuid.NAMESPACE_URL

SWISSUBASE_COLLECTION = "swissubase_entities"


def _chunk_id(entity_type: str, entity_id: str, chunk_index: int) -> str:
    return str(
        uuid.uuid5(_CHUNK_NAMESPACE, f"{entity_type}|{entity_id}|{chunk_index}"),
    )


def _row_text(entity_type: str, row: dict[str, Any]) -> str | None:
    """Build the canonical embedding text for a row.

    Each entity type has its own composition. We deliberately keep this
    narrow — title + abstract for studies; name + description for
    datasets; name + (optional) institution for persons; name + address
    for institutions. The vector tracks "what this entity *is*", not its
    full record.
    """
    if entity_type in ("studies", "datasets"):
        parts = [row.get("title"), row.get("description")]
    elif entity_type == "persons":
        parts = [row.get("display_name"), row.get("affiliation")]
    elif entity_type == "institutions":
        parts = [row.get("name"), row.get("address")]
    else:
        return None
    cleaned = [p for p in parts if isinstance(p, str) and p.strip()]
    if not cleaned:
        return None
    return "\n\n".join(cleaned)


def _row_to_payload(entity_type: str, row: dict[str, Any]) -> dict[str, Any]:
    """Common Qdrant payload shared across entity types.

    Always includes ``source_url`` (when known) so the LLM tool can
    return a verbatim canonical SWISSUbase URL — this is the project's
    hard requirement.
    """
    if entity_type == "studies":
        entity_id = row["study_id"]
        end_date = row.get("end_date")
        start_date = row.get("start_date")
        return {
            "entity_type": "studies",
            "entity_id": entity_id,
            "study_id": entity_id,
            "ref": row.get("ref"),
            "title": row.get("title"),
            "main_discipline": row.get("main_discipline"),
            "sub_discipline": row.get("sub_discipline"),
            "progress": row.get("progress"),
            "year_start": start_date.year if start_date else None,
            "year_end": end_date.year if end_date else None,
            "dataset_count": row.get("dataset_count"),
            "source_url": row["source_url"],
        }
    if entity_type == "datasets":
        entity_id = row["dataset_id"]
        return {
            "entity_type": "datasets",
            "entity_id": entity_id,
            "dataset_id": entity_id,
            "study_id": row.get("study_id"),
            "title": row.get("title"),
            "access_right": row.get("access_right"),
            "license_id": row.get("license_id"),
            "source_url": row["source_url"],
        }
    if entity_type == "persons":
        entity_id = row["person_key"]
        return {
            "entity_type": "persons",
            "entity_id": entity_id,
            "person_key": entity_id,
            "display_name": row.get("display_name"),
            "orcid": row.get("orcid"),
            "affiliation": row.get("affiliation"),
            "source_url": row.get("source_url"),
        }
    if entity_type == "institutions":
        entity_id = row["institution_key"]
        return {
            "entity_type": "institutions",
            "entity_id": entity_id,
            "institution_key": entity_id,
            "name": row.get("name"),
            "ror_id": row.get("ror_id"),
            "source_url": row.get("source_url"),
        }
    message = f"Unknown entity_type: {entity_type}"
    raise ValueError(message)


def _row_entity_id(entity_type: str, row: dict[str, Any]) -> str:
    if entity_type == "studies":
        return row["study_id"]
    if entity_type == "datasets":
        return row["dataset_id"]
    if entity_type == "persons":
        return row["person_key"]
    if entity_type == "institutions":
        return row["institution_key"]
    message = f"Unknown entity_type: {entity_type}"
    raise ValueError(message)


async def _embed_async(
    *,
    config: SwissubaseIndexConfig,
    store: SwissubaseStore,
    entity_types: tuple[str, ...],
    limit: int | None,
) -> dict[str, int]:
    client = RCPEmbeddingClient(config)  # type: ignore[arg-type]
    qdrant = QdrantStore(config)  # type: ignore[arg-type]
    qdrant.ensure_collection(SWISSUBASE_COLLECTION)

    summary: dict[str, int] = dict.fromkeys(entity_types, 0)

    for entity_type in entity_types:
        pending: list[tuple[str, dict[str, Any], Chunk]] = []

        # Bind both `et` and `buf` via defaults so the closure doesn't
        # late-resolve free variables from the loop scope (B023).
        async def flush(et: str = entity_type, buf: list = pending) -> None:
            if not buf:
                return
            texts = [c.text for _, _, c in buf]
            vectors = await client.embed_all(texts)
            ids: list[str] = []
            payloads: list[dict[str, Any]] = []
            for entity_id, base_payload, chunk in buf:
                cid = _chunk_id(et, entity_id, chunk.index)
                ids.append(cid)
                payloads.append({**base_payload, "chunk_index": chunk.index})
                store.upsert_chunk(
                    chunk_id=cid,
                    entity_type=et,
                    entity_id=entity_id,
                    chunk_index=chunk.index,
                    text=chunk.text,
                    token_count=chunk.token_count,
                    vector_id=cid,
                )
            qdrant.upsert_points(
                SWISSUBASE_COLLECTION,
                ids=ids,
                vectors=vectors,
                payloads=payloads,
            )
            summary[et] += len(buf)
            buf.clear()

        for row in store.stream_rows_for_embedding(entity_type, limit=limit):
            text = _row_text(entity_type, row)
            if not text:
                continue
            chunks = chunk_text(
                text,
                chunk_tokens=config.chunking.size_tokens,
                overlap=config.chunking.overlap_tokens,
            )
            if not chunks:
                continue
            entity_id = _row_entity_id(entity_type, row)
            base_payload = _row_to_payload(entity_type, row)
            for chunk in chunks:
                pending.append((entity_id, base_payload, chunk))
                if len(pending) >= client.batch_size:
                    await flush()
        await flush()
        LOGGER.info(
            "swissubase embed %s complete: chunks=%d",
            entity_type, summary[entity_type],
        )

    return summary


def embed_entities(
    *,
    config: SwissubaseIndexConfig,
    store: SwissubaseStore,
    entity_types: tuple[str, ...] | None = None,
    limit: int | None = None,
) -> dict[str, int]:
    """Synchronously embed SWISSUbase entities (any subset of the four)."""
    types = entity_types or tuple(EMBEDDABLE_ENTITY_TYPES)
    return asyncio.run(
        _embed_async(
            config=config, store=store,
            entity_types=types, limit=limit,
        ),
    )
