"""Pydantic-AI Tool backed by :class:`SnsfRagProvider`."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic_ai import Tool

from src.v2.ingest.providers.snsf_rag import (
    ScopeMode,  # noqa: TC001 — runtime annotation read by pydantic-ai
)
from src.v2.observation.query_log import record_query

if TYPE_CHECKING:
    from src.v2.ingest.providers.snsf_rag import SnsfRagProvider

logger = logging.getLogger(__name__)

_DESCRIPTION = (
    "Semantic search over the SNSF P3 (Swiss National Science Foundation) "
    "RAG index — every grant funded by SNSF since 1975, with title, "
    "abstract, keywords, discipline, host institution, PI name, dates, "
    "funded amount. Use this when README / CITATION / a researcher's "
    "page mentions SNSF funding, a project name that sounds like a Swiss "
    "research grant, or to ground 'who funded this' / 'what else has X "
    "been funded for' questions for CH-resident researchers. "
    "`scope_mode`: 'switzerland' (default — full ~90 k corpus, 1975-2027), "
    "'epfl' (~6 k EPFL-only precision view), 'ethz' (~9 k ETHZ-only), or "
    "'eth_domain' (placeholder; not embedded as its own collection — falls "
    "back to []). `filters`: dict with optional keys: `institution` "
    "(exact match on `research_institution`, e.g. 'EPF Lausanne – EPFL' "
    "or 'ETH Zurich – ETHZ'), `institute` (substring match on the "
    "specific lab/centre name resolved from DuckDB after the ANN — "
    "useful for SDSC-style queries: `{\"institute\": \"Swiss Data Science "
    "Center\"}`), `discipline_l1` (e.g. 'Mathematics, Informatics, "
    "Natural Sciences and Technology'), `state` (PascalCase: 'Completed' "
    "/ 'Ongoing' / 'Approved'). Other keys are dropped with a warning. "
    "`rerank=true` runs the cross-encoder against title+keywords+abstract. "
    "Returns thin hits with grant_number, title, research_institution, "
    "main_discipline, start_date, amount_granted, score, plus a snippet "
    "and the canonical https://data.snf.ch/grants/grant/<id> URL."
)


def make_snsf_rag_search_tool(provider: SnsfRagProvider) -> Tool:
    """Tool factory: semantic search over the SNSF P3 RAG index."""

    async def search_snsf_rag(
        query: str,
        scope_mode: ScopeMode = "switzerland",
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
        rerank: bool = False,  # noqa: FBT001, FBT002 — part of the LLM tool signature
    ) -> list[dict[str, Any]]:
        """Vector search the SNSF P3 index. See tool description."""
        logger.info(
            "tool call: search_snsf_rag — scope=%s top_k=%d rerank=%s "
            "filters=%r query=%r",
            scope_mode, top_k, rerank, filters, query,
        )
        record_query(
            service=f"snsf.rag.search.{scope_mode}",
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
        search_snsf_rag,
        name="search_snsf_rag",
        description=_DESCRIPTION,
    )


__all__ = ["make_snsf_rag_search_tool"]
