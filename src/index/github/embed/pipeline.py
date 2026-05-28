"""Embed GitHub repositories into Qdrant via the RCP `/embeddings` endpoint.

Reuses `RCPEmbeddingClient`, `QdrantStore`, and the token chunker from
`src.index.openalex` directly — those modules only access `config.rcp.*`
and `config.qdrant.*` at runtime, both of which `GitHubIndexConfig`
mirrors field-for-field. Same pattern as `src.index.zenodo`.

Two entry points:

- `embed_repos` — chunk + embed un-embedded repos (skip rows already in `chunks`).
- `rebuild_qdrant_from_chunks` — re-derive Qdrant points from existing `chunks`
  rows; used after a Qdrant wipe. Does not touch DuckDB.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.index.openalex.embed.chunker import Chunk, chunk_text
from src.index.openalex.embed.rcp_client import RCPEmbeddingClient
from src.index.openalex.vector.qdrant_store import QdrantStore

# Qdrant upsert retry policy. The qdrant_client default 5s read timeout is too
# tight under sustained load; transient timeouts shouldn't kill a multi-hour
# embed run. Exponential backoff: 5s, 15s, 45s, 135s — total ~3.5min before
# giving up on a single batch.
_QDRANT_RETRY_DELAYS_SECONDS: tuple[int, ...] = (5, 15, 45, 135)

if TYPE_CHECKING:
    from src.index.github.config import GitHubIndexConfig
    from src.index.github.storage.duckdb_store import GitHubStore

LOGGER = logging.getLogger(__name__)

_CHUNK_NAMESPACE = uuid.NAMESPACE_URL

GITHUB_COLLECTION = "github_repos"


def _chunk_id(entity_type: str, entity_id: str, chunk_index: int) -> str:
    return str(
        uuid.uuid5(_CHUNK_NAMESPACE, f"{entity_type}|{entity_id}|{chunk_index}"),
    )


def _row_topics(row: dict[str, Any]) -> list[str]:
    raw = row.get("topics")
    if isinstance(raw, str):
        try:
            return list(json.loads(raw) or [])
        except json.JSONDecodeError:
            return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    return []


def _readme_text(*, cards_dir: Path, readme_path: str | None) -> str | None:
    if not readme_path:
        return None
    abs_path = cards_dir / readme_path
    if not abs_path.exists():
        return None
    try:
        return abs_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _row_to_chunks(
    row: dict[str, Any],
    *,
    cards_dir: Path,
    chunk_tokens: int,
    overlap: int,
    min_card_chars: int,
) -> list[Chunk]:
    parts: list[str] = [str(row["repo_id"])]
    if row.get("description"):
        parts.append(str(row["description"]))
    topics = _row_topics(row)
    if topics:
        parts.append("topics: " + ", ".join(topics))
    readme = _readme_text(cards_dir=cards_dir, readme_path=row.get("readme_path"))
    if readme:
        parts.append(readme)
    text = "\n\n".join(parts)
    if len(text) < min_card_chars:
        return []
    return chunk_text(text, chunk_tokens=chunk_tokens, overlap=overlap)


def _row_to_payload(row: dict[str, Any]) -> dict[str, Any]:
    pushed = row.get("pushed_at")
    return {
        "entity_type": "repos",
        "entity_id": row["repo_id"],
        "repo_id": row["repo_id"],
        "owner": row.get("owner"),
        "name": row.get("name"),
        "primary_language": row.get("primary_language"),
        "license_spdx": row.get("license_spdx"),
        "stars": row.get("stargazers_count"),
        "forks": row.get("forks_count"),
        "is_archived": row.get("is_archived"),
        "is_fork": row.get("is_fork"),
        "pushed_at": pushed.isoformat() if hasattr(pushed, "isoformat") else pushed,
    }


async def _embed_repos_async(
    *,
    config: GitHubIndexConfig,
    store: GitHubStore,
    limit: int | None,
) -> int:
    client = RCPEmbeddingClient(config)  # type: ignore[arg-type]
    qdrant = QdrantStore(config)  # type: ignore[arg-type]
    qdrant.ensure_collection(GITHUB_COLLECTION)

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
            cid = _chunk_id("repos", entity_id, chunk.index)
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
                    GITHUB_COLLECTION,
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
                entity_type="repos",
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
    for row in store.stream_rows_for_embedding("repos", limit=limit):
        rows_seen += 1
        chunks = _row_to_chunks(
            row,
            cards_dir=config.paths.cards_dir,
            chunk_tokens=config.chunking.size_tokens,
            overlap=config.chunking.overlap_tokens,
            min_card_chars=config.github.min_card_chars,
        )
        if not chunks:
            rows_skipped += 1
            continue
        base_payload = _row_to_payload(row)
        for chunk in chunks:
            pending.append((row["repo_id"], base_payload, chunk))
            if len(pending) >= client.batch_size:
                await flush()
    await flush()
    LOGGER.info(
        "embed repos complete: rows_seen=%d skipped=%d chunks=%d",
        rows_seen,
        rows_skipped,
        total,
    )
    return total


def embed_repos(
    *,
    config: GitHubIndexConfig,
    store: GitHubStore,
    limit: int | None = None,
) -> dict[str, int]:
    """Synchronously embed GitHub repos."""
    chunks = asyncio.run(
        _embed_repos_async(config=config, store=store, limit=limit),
    )
    return {"repos": chunks}


# ---- Recovery from a Qdrant wipe ----------------------------------------


async def _rebuild_async(
    *,
    config: GitHubIndexConfig,
    store: GitHubStore,
) -> int:
    client = RCPEmbeddingClient(config)  # type: ignore[arg-type]
    qdrant = QdrantStore(config)  # type: ignore[arg-type]
    qdrant.ensure_collection(GITHUB_COLLECTION)

    cur = store.connect().execute(
        "SELECT c.chunk_id, c.entity_id, c.chunk_index, c.text, "
        "       r.owner, r.name, r.primary_language, r.license_spdx, "
        "       r.stargazers_count, r.forks_count, r.is_archived, r.is_fork, "
        "       r.pushed_at "
        "FROM chunks c JOIN repos r ON r.repo_id = c.entity_id "
        "WHERE c.entity_type = 'repos' "
        "ORDER BY c.entity_id, c.chunk_index",
    )
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]
    if not rows:
        LOGGER.info("rebuild repos: no chunks to push")
        return 0

    pushed = 0
    flush_size = client.batch_size
    for start in range(0, len(rows), flush_size):
        batch = rows[start : start + flush_size]
        texts = [r["text"] for r in batch]
        vectors = await client.embed_all(texts)
        ids = [r["chunk_id"] for r in batch]
        payloads: list[dict[str, Any]] = []
        for r in batch:
            pushed_at = r.get("pushed_at")
            payloads.append(
                {
                    "entity_type": "repos",
                    "entity_id": r["entity_id"],
                    "repo_id": r["entity_id"],
                    "owner": r.get("owner"),
                    "name": r.get("name"),
                    "primary_language": r.get("primary_language"),
                    "license_spdx": r.get("license_spdx"),
                    "stars": r.get("stargazers_count"),
                    "forks": r.get("forks_count"),
                    "is_archived": r.get("is_archived"),
                    "is_fork": r.get("is_fork"),
                    "pushed_at": pushed_at.isoformat() if hasattr(pushed_at, "isoformat") else pushed_at,
                    "chunk_index": r["chunk_index"],
                },
            )
        qdrant.upsert_points(
            GITHUB_COLLECTION,
            ids=ids,
            vectors=vectors,
            payloads=payloads,
        )
        pushed += len(batch)
        LOGGER.info("rebuild repos: pushed %d/%d points", pushed, len(rows))
    return pushed


def rebuild_qdrant_from_chunks(
    *,
    config: GitHubIndexConfig,
    store: GitHubStore,
) -> dict[str, int]:
    """Rebuild the `github_repos` collection from the existing `chunks` table.

    Re-embeds `chunks.text` via RCP and upserts to Qdrant under the existing
    `chunk_id` so the DuckDB ↔ Qdrant link is preserved. Used after a Qdrant
    wipe — does NOT modify DuckDB.
    """
    return {"repos": asyncio.run(_rebuild_async(config=config, store=store))}
