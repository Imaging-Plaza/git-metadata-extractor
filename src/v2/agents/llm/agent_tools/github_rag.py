"""Pydantic-AI Tool backed by :class:`GitHubRagProvider`."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic_ai import Tool

from src.v2.observation.query_log import record_query

if TYPE_CHECKING:
    from src.v2.ingest.providers.github_rag import GitHubRagProvider

logger = logging.getLogger(__name__)

_DESCRIPTION = (
    "Semantic search over the GitHub repositories RAG index — EPFL/Swiss "
    "research code repositories with metadata + README content embedded "
    "via Qwen3. Use this when you need to find related repositories, "
    "discover the canonical implementation of a technique mentioned in a "
    "paper or README, or surface forks/companions of the repo being "
    "processed. `filters`: optional dict of allowlisted keys "
    "(entity_type, repo_id, owner, primary_language, license_spdx, "
    "is_archived, is_fork). Each value may be a scalar, a list (any-of), "
    "or {$gte/$lte} for ranges. `rerank=true` engages the cross-encoder "
    "against repo_id+description — recommended when the query is a code "
    "concept rather than a literal repo name. Returns thin hits with "
    "repo_id, owner, name, primary_language, license_spdx, stars, forks, "
    "pushed_at, is_archived."
)


def make_github_rag_search_tool(provider: GitHubRagProvider) -> Tool:
    """Tool factory: semantic search over the GitHub RAG index."""

    async def search_github_rag(
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
        rerank: bool = False,  # noqa: FBT001, FBT002 — part of the LLM tool signature
    ) -> list[dict[str, Any]]:
        """Vector search the GitHub repositories index. See tool description."""
        logger.info(
            "tool call: search_github_rag — top_k=%d rerank=%s "
            "filters=%r query=%r",
            top_k, rerank, filters, query,
        )
        record_query(service="github.rag.search", query=query)
        return await provider.search(
            query, top_k=top_k, filters=filters, rerank=rerank,
        )

    return Tool(
        search_github_rag,
        name="search_github_rag",
        description=_DESCRIPTION,
    )


__all__ = ["make_github_rag_search_tool"]
