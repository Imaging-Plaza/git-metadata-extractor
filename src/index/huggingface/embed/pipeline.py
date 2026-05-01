"""Stream DuckDB rows → chunk card → embed → upsert into Qdrant.

Idempotent: rows whose card has already been embedded are skipped via
`DuckDBStore.stream_rows_for_embedding`.

The text fed to the embedder is built per-row from:

  - Title (always — `repo_id`),
  - Comma-joined tags,
  - Description from `card_data` (when present),
  - The README markdown.

Rows whose composite text is shorter than `huggingface.min_card_chars` are
skipped — many spaces ship with one-line placeholder READMEs that would
otherwise pollute the vector space.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import TYPE_CHECKING, Any

from src.index.huggingface.embed.chunker import Chunk, chunk_for_card
from src.index.huggingface.embed.rcp_client import RCPEmbeddingClient
from src.index.huggingface.ingest._common import description_from_card_data
from src.index.huggingface.models import ENTITY_TYPE_SINGULAR
from src.index.huggingface.vector.qdrant_store import COLLECTION_FOR_TABLE, QdrantStore

if TYPE_CHECKING:
    from src.index.huggingface.config import HuggingFaceIndexConfig
    from src.index.huggingface.storage.duckdb_store import DuckDBStore

LOGGER = logging.getLogger(__name__)

_CHUNK_NAMESPACE = uuid.NAMESPACE_URL


def _chunk_id(entity_type_singular: str, repo_id: str, chunk_index: int) -> str:
    return str(
        uuid.uuid5(
            _CHUNK_NAMESPACE,
            f"{entity_type_singular}|{repo_id}|{chunk_index}",
        ),
    )


def _readme_for(config: HuggingFaceIndexConfig, *, entity_table: str, repo_id: str) -> str | None:
    path = config.paths.cards_path_for(entity_table, repo_id) / "README.md"
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _row_to_chunks(
    *,
    entity_table: str,
    row: dict[str, Any],
    config: HuggingFaceIndexConfig,
    chunk_tokens: int,
    overlap: int,
) -> list[Chunk]:
    repo_id = row["repo_id"]
    tags = _coerce_tags(row.get("tags"))
    card_data = _coerce_json(row.get("card_data"))
    description = description_from_card_data(card_data)
    readme = _readme_for(config, entity_table=entity_table, repo_id=repo_id)

    composite_chars = sum(
        len(s)
        for s in (
            repo_id,
            ", ".join(tags or []),
            description or "",
            readme or "",
        )
    )
    if composite_chars < config.huggingface.min_card_chars:
        return []
    return chunk_for_card(
        title=repo_id,
        tags=tags,
        description=description,
        readme=readme,
        chunk_tokens=chunk_tokens,
        overlap=overlap,
        encoding_name=config.chunking.tokenizer,
    )


def _coerce_tags(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return [str(t) for t in value if t]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return None
        if isinstance(parsed, list):
            return [str(t) for t in parsed if t]
    return None


def _coerce_json(value: Any) -> dict | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _row_to_payload(*, entity_type_singular: str, row: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "entity_type": entity_type_singular,
        "repo_id": row["repo_id"],
        "author": row.get("author"),
        "license": row.get("license"),
        "downloads": row.get("downloads"),
    }
    last_modified = row.get("last_modified")
    if last_modified is not None:
        payload["last_modified"] = (
            last_modified.isoformat() if hasattr(last_modified, "isoformat") else str(last_modified)
        )
    if entity_type_singular == "model":
        payload["pipeline_tag"] = row.get("pipeline_tag")
        payload["library_name"] = row.get("library_name")
    if entity_type_singular == "space":
        payload["sdk"] = row.get("sdk")
    return payload


async def _embed_table_async(
    *,
    config: HuggingFaceIndexConfig,
    store: DuckDBStore,
    entity_table: str,
    limit: int | None,
) -> int:
    client = RCPEmbeddingClient(config)
    qdrant = QdrantStore(config)
    collection = COLLECTION_FOR_TABLE[entity_table]
    qdrant.ensure_collection(collection)
    singular = ENTITY_TYPE_SINGULAR[entity_table]  # type: ignore[index]

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
        for repo_id, base_payload, chunk in pending:
            cid = _chunk_id(singular, repo_id, chunk.index)
            ids.append(cid)
            payloads.append({**base_payload, "chunk_index": chunk.index})
            store.upsert_chunk(
                chunk_id=cid,
                entity_type=singular,
                repo_id=repo_id,
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
        total_chunks += len(pending)
        pending.clear()

    rows_seen = 0
    for row in store.stream_rows_for_embedding(entity_table, limit=limit):
        rows_seen += 1
        chunks = _row_to_chunks(
            entity_table=entity_table,
            row=row,
            config=config,
            chunk_tokens=config.chunking.size_tokens,
            overlap=config.chunking.overlap_tokens,
        )
        if not chunks:
            continue
        base_payload = _row_to_payload(entity_type_singular=singular, row=row)
        for chunk in chunks:
            pending.append((row["repo_id"], base_payload, chunk))
            if len(pending) >= client.batch_size:
                await flush()
    await flush()
    LOGGER.info(
        "embed %s complete: rows_seen=%d chunks=%d",
        entity_table,
        rows_seen,
        total_chunks,
    )
    return total_chunks


def _org_to_chunks(
    *,
    org_row: dict[str, Any],
    repos_by_table: dict[str, list[dict[str, Any]]],
    config: HuggingFaceIndexConfig,
    chunk_tokens: int,
    overlap: int,
) -> list[Chunk]:
    """Build the embed text for a namespace from its overview + repo titles/tags."""
    slug = org_row["slug"]
    fullname = org_row.get("fullname") or ""
    details = org_row.get("details") or ""
    kind = org_row.get("namespace_kind") or "org"

    sections: list[str] = []
    for table, repos in repos_by_table.items():
        if not repos:
            continue
        lines: list[str] = []
        for repo in repos:
            tags = _coerce_tags(repo.get("tags")) or []
            card_data = _coerce_json(repo.get("card_data")) or {}
            description = description_from_card_data(card_data) or ""
            tag_str = ", ".join(tags[:8])  # cap to keep signal:noise high
            line = repo["repo_id"]
            if tag_str:
                line += f" [{tag_str}]"
            if description:
                line += f" — {description[:160]}"
            lines.append(line)
        sections.append(f"{table.capitalize()}:\n" + "\n".join(lines))

    description = details if details else fullname
    readme = "\n\n".join(sections) if sections else None

    composite_chars = sum(
        len(s) for s in (slug, fullname, details, readme or "")
    )
    if composite_chars < config.huggingface.min_card_chars:
        return []
    title = f"{slug} ({kind})" if not fullname else f"{slug} — {fullname} ({kind})"
    return chunk_for_card(
        title=title,
        tags=None,
        description=description or None,
        readme=readme,
        chunk_tokens=chunk_tokens,
        overlap=overlap,
        encoding_name=config.chunking.tokenizer,
    )


def _org_to_payload(*, org_row: dict[str, Any]) -> dict[str, Any]:
    return {
        "entity_type": "org",
        "repo_id": org_row["slug"],          # keep payload key consistent across collections
        "slug": org_row["slug"],
        "namespace_kind": org_row.get("namespace_kind"),
        "scope": org_row.get("scope"),
        "fullname": org_row.get("fullname"),
        "num_models": org_row.get("num_models"),
        "num_datasets": org_row.get("num_datasets"),
        "num_spaces": org_row.get("num_spaces"),
        "num_followers": org_row.get("num_followers"),
    }


async def _embed_orgs_async(
    *,
    config: HuggingFaceIndexConfig,
    store: DuckDBStore,
    limit: int | None,
) -> int:
    client = RCPEmbeddingClient(config)
    qdrant = QdrantStore(config)
    collection = COLLECTION_FOR_TABLE["orgs"]
    qdrant.ensure_collection(collection)

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
        for slug, base_payload, chunk in pending:
            cid = _chunk_id("org", slug, chunk.index)
            ids.append(cid)
            payloads.append({**base_payload, "chunk_index": chunk.index})
            store.upsert_chunk(
                chunk_id=cid,
                entity_type="org",
                repo_id=slug,
                chunk_index=chunk.index,
                text=chunk.text,
                token_count=chunk.token_count,
                vector_id=cid,
            )
        qdrant.upsert_points(collection, ids=ids, vectors=vectors, payloads=payloads)
        total_chunks += len(pending)
        pending.clear()

    rows_seen = 0
    for row in store.stream_orgs_for_embedding(limit=limit):
        rows_seen += 1
        repos_by_table = store.list_repo_titles_for_org(row["slug"])
        chunks = _org_to_chunks(
            org_row=row,
            repos_by_table=repos_by_table,
            config=config,
            chunk_tokens=config.chunking.size_tokens,
            overlap=config.chunking.overlap_tokens,
        )
        if not chunks:
            continue
        base_payload = _org_to_payload(org_row=row)
        for chunk in chunks:
            pending.append((row["slug"], base_payload, chunk))
            if len(pending) >= client.batch_size:
                await flush()
    await flush()
    LOGGER.info("embed orgs complete: rows_seen=%d chunks=%d", rows_seen, total_chunks)
    return total_chunks


def embed_entities(
    *,
    config: HuggingFaceIndexConfig,
    store: DuckDBStore,
    entity_tables: list[str],
    limit: int | None = None,
) -> dict[str, int]:
    """Synchronously embed across the requested entity tables."""
    summary: dict[str, int] = {}
    for table in entity_tables:
        if table == "orgs":
            summary[table] = asyncio.run(
                _embed_orgs_async(config=config, store=store, limit=limit),
            )
        else:
            summary[table] = asyncio.run(
                _embed_table_async(
                    config=config,
                    store=store,
                    entity_table=table,
                    limit=limit,
                ),
            )
    return summary


__all__ = ["embed_entities", "_chunk_id"]
