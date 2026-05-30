"""Embed RenkuLab entities into Qdrant via the RCP `/embeddings` endpoint.

Reuses `RCPEmbeddingClient`, `QdrantStore`, and the token chunker from
`src.index.openalex` directly — those modules access only `config.rcp.*`
and `config.qdrant.*` at runtime, both of which `RenkulabIndexConfig`
mirrors field-for-field.

Each entity type lands in its own Qdrant collection so retrieval can
filter by entity without scanning the wider corpus:

    renkulab_projects        — projects (name + description + keywords)
    renkulab_groups          — groups
    renkulab_users           — users (first/last name + path)
    renkulab_data_connectors — data connectors (name + description + storage type)
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import TYPE_CHECKING, Any

from src.index.openalex.embed.chunker import Chunk, chunk_text
from src.index._rcp.embed_client import RCPEmbeddingClient
from src.index.openalex.vector.qdrant_store import QdrantStore

if TYPE_CHECKING:
    from src.index.renkulab.config import RenkulabIndexConfig
    from src.index.renkulab.storage.duckdb_store import RenkulabStore

LOGGER = logging.getLogger(__name__)

_CHUNK_NAMESPACE = uuid.NAMESPACE_URL

COLLECTION_BY_ENTITY: dict[str, str] = {
    "projects": "renkulab_projects",
    "groups": "renkulab_groups",
    "users": "renkulab_users",
    "data_connectors": "renkulab_data_connectors",
}

_PK_BY_ENTITY: dict[str, str] = {
    "projects": "project_id",
    "groups": "group_id",
    "users": "user_id",
    "data_connectors": "data_connector_id",
}


def _chunk_id(entity_type: str, entity_id: str, chunk_index: int) -> str:
    return str(
        uuid.uuid5(_CHUNK_NAMESPACE, f"{entity_type}|{entity_id}|{chunk_index}"),
    )


def _decode_json(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return []
        return decoded if isinstance(decoded, list) else []
    return []


def _row_to_text(entity_type: str, row: dict[str, Any]) -> str | None:
    parts: list[str] = []
    if entity_type == "projects":
        for k in ("name", "namespace", "description"):
            v = row.get(k)
            if v:
                parts.append(str(v))
        keywords = _decode_json(row.get("keywords_json"))
        if keywords:
            parts.append("Keywords: " + ", ".join(str(k) for k in keywords))
    elif entity_type == "groups":
        for k in ("name", "slug", "description"):
            v = row.get(k)
            if v:
                parts.append(str(v))
    elif entity_type == "users":
        first = row.get("first_name") or ""
        last = row.get("last_name") or ""
        full = (f"{first} {last}").strip()
        if full:
            parts.append(full)
        for k in ("path", "slug"):
            v = row.get(k)
            if v and v != full:
                parts.append(str(v))
    elif entity_type == "data_connectors":
        for k in ("name", "namespace", "description"):
            v = row.get(k)
            if v:
                parts.append(str(v))
        storage_type = row.get("storage_type")
        if storage_type:
            parts.append(f"Storage: {storage_type}")
        keywords = _decode_json(row.get("keywords_json"))
        if keywords:
            parts.append("Keywords: " + ", ".join(str(k) for k in keywords))
    if not parts:
        return None
    return "\n\n".join(parts)


def _row_to_payload(entity_type: str, row: dict[str, Any]) -> dict[str, Any]:
    pk = _PK_BY_ENTITY[entity_type]
    payload: dict[str, Any] = {
        "entity_type": entity_type,
        "entity_id": str(row[pk]),
    }
    if entity_type == "projects":
        payload.update(
            {
                "name": row.get("name"),
                "slug": row.get("slug"),
                "namespace": row.get("namespace"),
                "path": row.get("path"),
                "visibility": row.get("visibility"),
            },
        )
    elif entity_type == "groups":
        payload.update({"name": row.get("name"), "slug": row.get("slug")})
    elif entity_type == "users":
        payload.update(
            {
                "first_name": row.get("first_name"),
                "last_name": row.get("last_name"),
                "slug": row.get("slug"),
                "path": row.get("path"),
            },
        )
    elif entity_type == "data_connectors":
        payload.update(
            {
                "name": row.get("name"),
                "slug": row.get("slug"),
                "namespace": row.get("namespace"),
                "path": row.get("path"),
                "storage_type": row.get("storage_type"),
                "storage_provider": row.get("storage_provider"),
                "visibility": row.get("visibility"),
            },
        )
    return payload


async def _embed_entity_async(
    *,
    config: RenkulabIndexConfig,
    store: RenkulabStore,
    entity_type: str,
    limit: int | None,
) -> int:
    client = RCPEmbeddingClient(config)  # type: ignore[arg-type]
    qdrant = QdrantStore(config)  # type: ignore[arg-type]
    collection = COLLECTION_BY_ENTITY[entity_type]
    qdrant.ensure_collection(collection)

    pk = _PK_BY_ENTITY[entity_type]
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
            cid = _chunk_id(entity_type, entity_id, chunk.index)
            ids.append(cid)
            payloads.append({**base_payload, "chunk_index": chunk.index})
            store.upsert_chunk(
                chunk_id=cid,
                entity_type=entity_type,
                entity_id=entity_id,
                chunk_index=chunk.index,
                text=chunk.text,
                token_count=chunk.token_count,
                vector_id=cid,
            )
        qdrant.upsert_points(
            collection,
            ids=ids,
            vectors=vectors,
            payloads=payloads,
        )
        total += len(pending)
        pending.clear()

    rows_seen = 0
    for row in store.stream_rows_for_embedding(entity_type, limit=limit):
        rows_seen += 1
        text = _row_to_text(entity_type, row)
        if not text:
            continue
        chunks = chunk_text(
            text,
            chunk_tokens=config.chunking.size_tokens,
            overlap=config.chunking.overlap_tokens,
        )
        if not chunks:
            continue
        base_payload = _row_to_payload(entity_type, row)
        entity_id = str(row[pk])
        for chunk in chunks:
            pending.append((entity_id, base_payload, chunk))
            if len(pending) >= client.batch_size:
                await flush()
    await flush()
    LOGGER.info(
        "embed %s complete: rows_seen=%d chunks=%d",
        entity_type,
        rows_seen,
        total,
    )
    return total


def embed_entities(
    *,
    config: RenkulabIndexConfig,
    store: RenkulabStore,
    entity_types: list[str] | None = None,
    limit: int | None = None,
) -> dict[str, int]:
    """Synchronously embed one or more entity types."""
    targets = entity_types or list(COLLECTION_BY_ENTITY)
    summary: dict[str, int] = {}
    for et in targets:
        if et not in COLLECTION_BY_ENTITY:
            message = (
                f"Unknown entity_type: {et!r}. "
                f"Known: {sorted(COLLECTION_BY_ENTITY)}"
            )
            raise ValueError(message)
        summary[et] = asyncio.run(
            _embed_entity_async(
                config=config,
                store=store,
                entity_type=et,
                limit=limit,
            ),
        )
    return summary
