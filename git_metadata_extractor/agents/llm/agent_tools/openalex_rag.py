"""Pydantic-AI Tool backed by :class:`OpenAlexRagProvider`."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic_ai import Tool

from git_metadata_extractor.providers.openalex_rag import (
    CollectionName,  # noqa: TC001 — runtime annotation read by pydantic-ai
)
from git_metadata_extractor.observation.query_log import record_query

if TYPE_CHECKING:
    from git_metadata_extractor.providers.openalex_rag import OpenAlexRagProvider

logger = logging.getLogger(__name__)

_DESCRIPTION = (
    "Semantic search over the OpenAlex RAG index — a worldwide scholarly "
    "graph (works, authors, institutions, sources, topics, concepts). Use "
    "this when you need to ground a paper, author, or institution that "
    "isn't in EPFL's Infoscience. `collection`: 'works' (default — papers; "
    "abstracts indexed), 'authors', 'institutions', 'sources', 'topics', "
    "'concepts'. `filters`: optional dict of allowlisted keys "
    "(year/publication_year, doi, entity_type, openalex_id, country_code, "
    "type, field_id, domain_id, level, primary_topic_id, "
    "primary_source_id, last_known_institution_id, orcid, ror). Each "
    "value may be a scalar, a list (any-of), or {$gte/$lte} for ranges. "
    "`rerank=true` engages the cross-encoder; for works it reranks "
    "against title+abstract — strong signal. Returns thin hits with "
    "openalex_id, title/display_name, doi/orcid/ror, year, plus a "
    "snippet (first ~320 chars of the abstract) for works."
)


def make_openalex_rag_search_tool(provider: OpenAlexRagProvider) -> Tool:
    """Tool factory: semantic search over the OpenAlex RAG index."""

    async def search_openalex_rag(
        query: str,
        collection: CollectionName = "works",
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
        rerank: bool = False,  # noqa: FBT001, FBT002 — part of the LLM tool signature
    ) -> list[dict[str, Any]]:
        """Vector search the OpenAlex index. See tool description."""
        logger.info(
            "tool call: search_openalex_rag — collection=%s top_k=%d "
            "rerank=%s filters=%r query=%r",
            collection, top_k, rerank, filters, query,
        )
        record_query(
            service=f"openalex.rag.search.{collection}",
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
        search_openalex_rag,
        name="search_openalex_rag",
        description=_DESCRIPTION,
    )


__all__ = ["make_openalex_rag_search_tool"]
