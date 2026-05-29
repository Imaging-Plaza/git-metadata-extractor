"""Semantic retrieval over the GitHub index.

Embed → Qdrant search → RCP rerank → DuckDB hydrate. Reuses the openalex
RCP + Qdrant clients directly.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from src.index.github_repos.embed.pipeline import GITHUB_REPOS_COLLECTION
from src.index.github_repos.storage.duckdb_store import GitHubReposStore
from src.index.openalex.embed.rcp_client import RCPEmbeddingClient
from src.index.openalex.rerank.rcp_client import RCPRerankerClient
from src.index.openalex.vector.qdrant_store import QdrantStore

if TYPE_CHECKING:
    from src.index.github_repos.config import GitHubIndexConfig

LOGGER = logging.getLogger(__name__)


def _payload_to_doc(payload: dict[str, Any]) -> str:
    repo_id = payload.get("repo_id") or payload.get("entity_id")
    if repo_id:
        return str(repo_id)
    return json.dumps(payload, ensure_ascii=False)


async def _async_search(
    *,
    config: GitHubIndexConfig,
    query: str,
    top_k: int,
    candidate_k: int,
    filter_payload: dict[str, Any] | None,
    store: GitHubReposStore,
) -> list[dict[str, Any]]:
    embed = RCPEmbeddingClient(config)  # type: ignore[arg-type]
    qdrant = QdrantStore(config)  # type: ignore[arg-type]
    rerank = RCPRerankerClient(config)  # type: ignore[arg-type]

    [query_vec] = await embed.embed_all([query])
    candidates = qdrant.search(
        GITHUB_REPOS_COLLECTION,
        query_vector=query_vec,
        top_k=candidate_k,
        filter_payload=filter_payload,
    )
    if not candidates:
        return []

    docs = [_payload_to_doc(c["payload"]) for c in candidates]
    reranked = await rerank.rerank(query, docs, top_n=top_k)
    if not reranked:
        ordered = candidates[:top_k]
    else:
        ordered = [
            {**candidates[r["index"]], "rerank_score": r["relevance_score"]}
            for r in reranked
        ]

    hydrated: list[dict[str, Any]] = []
    for hit in ordered:
        payload = hit["payload"] or {}
        repo_id = payload.get("repo_id") or payload.get("entity_id")
        if not repo_id:
            continue
        hydrated.append(
            {
                "id": hit["id"],
                "vector_score": hit["score"],
                "rerank_score": hit.get("rerank_score"),
                "payload": payload,
                "entity": store.fetch_repo(str(repo_id)),
            },
        )
    return hydrated


def semantic_search(
    *,
    config: GitHubIndexConfig,
    query: str,
    top_k: int = 10,
    candidate_k: int = 50,
    filter_payload: dict[str, Any] | None = None,
    store: GitHubReposStore | None = None,
) -> list[dict[str, Any]]:
    if store is None:
        store = GitHubReposStore.open()
    return asyncio.run(
        _async_search(
            config=config,
            query=query,
            top_k=top_k,
            candidate_k=candidate_k,
            filter_payload=filter_payload,
            store=store,
        ),
    )
