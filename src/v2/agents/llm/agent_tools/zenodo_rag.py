"""Pydantic-AI Tool backed by :class:`ZenodoRagProvider`."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic_ai import Tool

from src.v2.observation.query_log import record_query

if TYPE_CHECKING:
    from src.v2.ingest.providers.zenodo_rag import ZenodoRagProvider

logger = logging.getLogger(__name__)

_DESCRIPTION = (
    "Semantic search over the Zenodo RAG index — datasets, software, "
    "papers, and other research outputs deposited to Zenodo. Use this when "
    "a repository or paper references a Zenodo DOI / dataset and you need "
    "the canonical record. `filters`: optional dict of allowlisted keys "
    "(year, doi, resource_type, access_right, entity_type, zenodo_id). "
    "Each value may be a scalar, a list (any-of), or {$gte/$lte} for "
    "ranges. `rerank=true` engages the cross-encoder against title+"
    "description — strong signal. Returns thin hits with zenodo_id, "
    "title, doi, year, resource_type, access_right."
)


def make_zenodo_rag_search_tool(provider: ZenodoRagProvider) -> Tool:
    """Tool factory: semantic search over the Zenodo RAG index."""

    async def search_zenodo_rag(
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
        rerank: bool = False,  # noqa: FBT001, FBT002 — part of the LLM tool signature
    ) -> list[dict[str, Any]]:
        """Vector search the Zenodo index. See tool description."""
        logger.info(
            "tool call: search_zenodo_rag — top_k=%d rerank=%s "
            "filters=%r query=%r",
            top_k, rerank, filters, query,
        )
        record_query(service="zenodo.rag.search", query=query)
        return await provider.search(
            query, top_k=top_k, filters=filters, rerank=rerank,
        )

    return Tool(
        search_zenodo_rag,
        name="search_zenodo_rag",
        description=_DESCRIPTION,
    )


__all__ = ["make_zenodo_rag_search_tool"]
