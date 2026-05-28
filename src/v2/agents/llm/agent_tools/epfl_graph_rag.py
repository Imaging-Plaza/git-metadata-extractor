"""Pydantic-AI Tool backed by :class:`EpflGraphRagProvider`.

Surfaces the EPFL Graph academic discipline ontology as a semantic-search
tool: given any text (a README, abstract, project description) the LLM
can ask for the closest disciplines from the curated ~2226-node tree.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic_ai import Tool

from src.v2.observation.query_log import record_query

if TYPE_CHECKING:
    from src.v2.ingest.providers.epfl_graph_rag import EpflGraphRagProvider

logger = logging.getLogger(__name__)

_DESCRIPTION = (
    "Semantic search over the EPFL Graph academic-discipline ontology "
    "(~2226 categories, depth 1..5, each backed by 50-110 anchor "
    "Wikipedia articles). Use this to map a README, abstract or project "
    "description onto canonical EPFL disciplines like "
    "`topics-in-natural-language-processing` or `computer-graphics`. "
    "`filters`: optional dict of allowlisted keys (category_id, depth, "
    "parent_id, entity_type). Each value may be a scalar, a list "
    "(any-of), or {$gte/$lte} for ranges — use depth>=4 to keep only "
    "leaf disciplines. `rerank=true` engages the cross-encoder against "
    "name + anchor concepts. Returns thin hits with category_id, name, "
    "depth, parent_id, wikipedia_url, graphsearch_url."
)


def make_epfl_graph_rag_search_tool(provider: EpflGraphRagProvider) -> Tool:
    """Tool factory: semantic search over the EPFL Graph disciplines index."""

    async def search_epfl_graph_disciplines(
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
        rerank: bool = False,  # noqa: FBT001, FBT002 — part of the LLM tool signature
    ) -> list[dict[str, Any]]:
        """Vector search the EPFL Graph disciplines index. See tool description."""
        logger.info(
            "tool call: search_epfl_graph_disciplines — top_k=%d rerank=%s "
            "filters=%r query=%r",
            top_k, rerank, filters, query,
        )
        record_query(service="epfl_graph.rag.search", query=query)
        return await provider.search(
            query, top_k=top_k, filters=filters, rerank=rerank,
        )

    return Tool(
        search_epfl_graph_disciplines,
        name="search_epfl_graph_disciplines",
        description=_DESCRIPTION,
    )


__all__ = ["make_epfl_graph_rag_search_tool"]
