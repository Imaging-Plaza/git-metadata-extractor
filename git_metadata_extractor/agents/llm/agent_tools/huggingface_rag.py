"""Pydantic-AI Tool backed by :class:`HuggingFaceRagProvider`."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic_ai import Tool

from git_metadata_extractor.providers.huggingface_rag import (
    CollectionName,  # noqa: TC001 — runtime annotation read by pydantic-ai
)
from git_metadata_extractor.observation.query_log import record_query

if TYPE_CHECKING:
    from git_metadata_extractor.providers.huggingface_rag import HuggingFaceRagProvider

logger = logging.getLogger(__name__)

_DESCRIPTION = (
    "Semantic search over the HuggingFace Hub RAG index (models / datasets "
    "/ spaces). Use this when README / CITATION / repo metadata mentions a "
    "model or dataset by name or capability and you need the canonical "
    "repo_id. `collection`: 'models' (default), 'datasets', or 'spaces'. "
    "`filters`: optional dict of allowlisted keys (library_name, "
    "pipeline_tag, sdk, gated, downloads, downloads_all_time, likes, "
    "author, license, entity_type). Each value may be a scalar, a list "
    "(any-of), or {$gte/$lte} for ranges. `rerank=true` engages the "
    "cross-encoder; HF chunks are metadata-only so rerank uses synthetic "
    "doc strings (repo_id+library/sdk) — useful for repo-name "
    "disambiguation, weaker for content relevance. Returns thin hits with "
    "repo_id, author, library_name/pipeline_tag/sdk, downloads, likes."
)


def make_huggingface_rag_search_tool(provider: HuggingFaceRagProvider) -> Tool:
    """Tool factory: semantic search over the HuggingFace RAG index."""

    async def search_huggingface_rag(
        query: str,
        collection: CollectionName = "models",
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
        rerank: bool = False,  # noqa: FBT001, FBT002 — part of the LLM tool signature
    ) -> list[dict[str, Any]]:
        """Vector search the HuggingFace Hub index. See tool description."""
        logger.info(
            "tool call: search_huggingface_rag — collection=%s top_k=%d "
            "rerank=%s filters=%r query=%r",
            collection, top_k, rerank, filters, query,
        )
        record_query(
            service=f"huggingface.rag.search.{collection}",
            query=query,
        )
        return await provider.search(
            query,
            collection=collection,
            top_k=top_k,
            filters=filters,
            rerank=rerank,
        )

    return Tool(
        search_huggingface_rag,
        name="search_huggingface_rag",
        description=_DESCRIPTION,
    )


__all__ = ["make_huggingface_rag_search_tool"]
