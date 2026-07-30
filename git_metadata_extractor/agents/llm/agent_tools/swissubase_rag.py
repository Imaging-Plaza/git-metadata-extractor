"""Pydantic-AI Tool backed by :class:`SwissubaseRagProvider`."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic_ai import Tool

from git_metadata_extractor.observation.query_log import record_query

if TYPE_CHECKING:
    from git_metadata_extractor.providers.swissubase_rag import SwissubaseRagProvider

logger = logging.getLogger(__name__)

_DESCRIPTION = (
    "Semantic search over the SWISSUbase RAG index — the Swiss "
    "national social-science research-data platform "
    "(www.swissubase.ch). Indexes studies (UI label: Project), "
    "their datasets, principal investigators, and partner "
    "institutions. Use this when a repository / paper / person "
    "claims affiliation with a Swiss social-science study, "
    "FORS-funded project, NCCR LIVES research, or similar. "
    "`filters`: optional dict of allowlisted keys "
    "(entity_type, study_id, dataset_id, person_key, "
    "institution_key, ref, main_discipline, sub_discipline, "
    "progress, year_start, year_end, access_right). Each value "
    "may be a scalar, a list (any-of), or {$gte/$lte} for ranges. "
    "Set `entity_type` to one of {studies, datasets, persons, "
    "institutions} to scope the search to a single bucket. "
    "`rerank=true` engages the cross-encoder. Each hit carries "
    "the canonical `source_url` (https://www.swissubase.ch/...) "
    "as a top-level field."
)


def make_swissubase_rag_search_tool(provider: SwissubaseRagProvider) -> Tool:
    """Tool factory: semantic search over the SWISSUbase RAG index."""

    async def search_swissubase_rag(
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
        rerank: bool = False,  # noqa: FBT001, FBT002 — LLM-facing signature
    ) -> list[dict[str, Any]]:
        """Vector search the SWISSUbase index. See tool description."""
        logger.info(
            "tool call: search_swissubase_rag — top_k=%d rerank=%s "
            "filters=%r query=%r",
            top_k, rerank, filters, query,
        )
        record_query(service="swissubase.rag.search", query=query)
        return await provider.search(
            query, top_k=top_k, filters=filters, rerank=rerank,
        )

    return Tool(
        search_swissubase_rag,
        name="search_swissubase_rag",
        description=_DESCRIPTION,
    )


__all__ = ["make_swissubase_rag_search_tool"]
