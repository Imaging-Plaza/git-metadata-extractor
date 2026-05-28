"""Pydantic-AI Tool backed by :class:`OrcidRagProvider`."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic_ai import Tool

from src.v2.ingest.providers.orcid_rag import (
    EntityType,  # noqa: TC001 — runtime annotation read by pydantic-ai
)
from src.v2.observation.query_log import record_query

if TYPE_CHECKING:
    from src.v2.ingest.providers.orcid_rag import OrcidRagProvider

logger = logging.getLogger(__name__)

_DESCRIPTION = (
    "Semantic search over the ORCID RAG index, scoped to the configured "
    "region (typically EPFL). Use this when you need to ground a person "
    "by name or biography to an ORCID iD, or to look up their employment / "
    "education history. `entity_type`: 'persons' (default — name + bio), "
    "'employments' (jobs), 'educations' (degrees). `filters`: optional "
    "dict of allowlisted keys (orcid_id, in_scope, discovered_via, "
    "org_ror, organization, department, role). `rerank=true` engages the "
    "cross-encoder; for persons it reranks against name + biography. "
    "Returns thin hits with orcid_id, names, organization/role for "
    "affiliations, plus a biography snippet for persons."
)


def make_orcid_rag_search_tool(provider: OrcidRagProvider) -> Tool:
    """Tool factory: semantic search over the ORCID RAG index."""

    async def search_orcid_rag(
        query: str,
        entity_type: EntityType = "persons",
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
        rerank: bool = False,  # noqa: FBT001, FBT002 — part of the LLM tool signature
    ) -> list[dict[str, Any]]:
        """Vector search the ORCID index. See tool description."""
        logger.info(
            "tool call: search_orcid_rag — entity=%s top_k=%d rerank=%s "
            "filters=%r query=%r",
            entity_type, top_k, rerank, filters, query,
        )
        record_query(
            service=f"orcid.rag.search.{entity_type}",
            query=query,
        )
        return await provider.search(
            query,
            entity_type=entity_type,
            top_k=top_k,
            filters=filters,
            rerank=rerank,
        )

    return Tool(
        search_orcid_rag,
        name="search_orcid_rag",
        description=_DESCRIPTION,
    )


__all__ = ["make_orcid_rag_search_tool"]
