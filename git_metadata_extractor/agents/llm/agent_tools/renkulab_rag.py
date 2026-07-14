"""Pydantic-AI Tool backed by :class:`RenkulabRagProvider`."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic_ai import Tool

from git_metadata_extractor.observation.query_log import record_query

if TYPE_CHECKING:
    from git_metadata_extractor.providers.renkulab_rag import RenkulabRagProvider

logger = logging.getLogger(__name__)

_DESCRIPTION = (
    "Semantic search over the RenkuLab RAG index (renkulab.io) — "
    "data-science projects, groups, users, and data connectors hosted "
    "by the Swiss Data Science Center. Use this when a repository, "
    "person, or organisation might be active on RenkuLab. "
    "`entity_types`: optional subset of "
    "['projects','groups','users','data_connectors']; defaults to all "
    "four collections (results merged + reranked together). "
    "`filters`: optional dict of allowlisted keys (entity_type, slug, "
    "namespace, path, visibility, storage_type, storage_provider, "
    "entity_id) — each value scalar / list (any-of) / {$gte,$lte}. "
    "`rerank=true` engages the cross-encoder against the entity's "
    "name + namespace — strong signal for ambiguous user / project "
    "names. Returns thin hits projected per entity type "
    "(projects: name+namespace+visibility; groups: name+slug; "
    "users: first/last name+path; data_connectors: name+namespace+"
    "storage_type) with the renkulab.io URL when known."
)


def make_renkulab_rag_search_tool(provider: RenkulabRagProvider) -> Tool:
    """Tool factory: semantic search over the RenkuLab RAG index."""

    async def search_renkulab_rag(
        query: str,
        top_k: int = 10,
        entity_types: list[str] | None = None,
        filters: dict[str, Any] | None = None,
        rerank: bool = False,  # noqa: FBT001, FBT002 — part of the LLM tool signature
    ) -> list[dict[str, Any]]:
        """Vector search the RenkuLab index. See tool description."""
        logger.info(
            "tool call: search_renkulab_rag — top_k=%d rerank=%s "
            "entity_types=%r filters=%r query=%r",
            top_k, rerank, entity_types, filters, query,
        )
        record_query(service="renkulab.rag.search", query=query)
        return await provider.search(
            query,
            top_k=top_k,
            entity_types=entity_types,
            filters=filters,
            rerank=rerank,
        )

    return Tool(
        search_renkulab_rag,
        name="search_renkulab_rag",
        description=_DESCRIPTION,
    )


__all__ = ["make_renkulab_rag_search_tool"]
