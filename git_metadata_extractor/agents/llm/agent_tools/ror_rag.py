"""Pydantic-AI Tool backed by :class:`RorRagProvider`."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic_ai import Tool

from git_metadata_extractor.providers.ror_rag import (
    ScopeMode,  # noqa: TC001 — runtime annotation read by pydantic-ai
)
from git_metadata_extractor.observation.query_log import record_query

if TYPE_CHECKING:
    from git_metadata_extractor.providers.ror_rag import RorRagProvider

logger = logging.getLogger(__name__)

_DESCRIPTION = (
    "Semantic search over the ROR (Research Organization Registry) RAG "
    "index. Use this when README / CITATION mentions a research lab, "
    "university, or institute by name and you need its canonical ROR id "
    "and parent organisation chain. `scope_mode`: 'worldwide' (default — "
    "global ROR), 'europe', 'switzerland', or 'epfl_ethz' (narrowest, "
    "EPFL+ETHZ neighbourhood). Filtering: only `country_code` is "
    "supported by the ROR store (e.g. 'CH', 'FR'); pass it via "
    '`filters={"country_code": "CH"}`. Other filter keys are dropped '
    "with a warning. `rerank=true` reranks against the indexed text "
    "(name + types + parent + website). Returns thin hits with ror_id, "
    "name, country_code, types, plus a short text snippet."
)


def make_ror_rag_search_tool(provider: RorRagProvider) -> Tool:
    """Tool factory: semantic search over the ROR RAG index."""

    async def search_ror_rag(
        query: str,
        scope_mode: ScopeMode = "worldwide",
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
        rerank: bool = False,  # noqa: FBT001, FBT002 — part of the LLM tool signature
    ) -> list[dict[str, Any]]:
        """Vector search the ROR index. See tool description."""
        logger.info(
            "tool call: search_ror_rag — scope=%s top_k=%d rerank=%s "
            "filters=%r query=%r",
            scope_mode, top_k, rerank, filters, query,
        )
        record_query(
            service=f"ror.rag.search.{scope_mode}",
            query=query,
        )
        return await provider.search(
            query,
            scope_mode=scope_mode,
            top_k=top_k,
            filters=filters,
            rerank=rerank,
        )

    return Tool(
        search_ror_rag,
        name="search_ror_rag",
        description=_DESCRIPTION,
    )


__all__ = ["make_ror_rag_search_tool"]
