"""Pydantic-AI Tools backed by :class:`ZenodoRagProvider`."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic_ai import Tool

from src.v2.observation.query_log import record_query

if TYPE_CHECKING:
    from src.v2.ingest.providers.zenodo_rag import ZenodoRagProvider

logger = logging.getLogger(__name__)

_SEARCH_DESCRIPTION = (
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

_FETCH_RECORDS_DESCRIPTION = (
    "Hydrate one or more Zenodo records by id. Each id may be a numeric "
    'Zenodo record id (e.g. "3909400") OR a `concept_recid` (the parent '
    'id Zenodo uses to group all versions of a deposit, e.g. "3909399") — '
    "concept ids are resolved to the most recent ingested version. Returns "
    "full payloads: zenodo_id, concept_recid, title, doi, publication_date, "
    "resource_type, access_right, license_id, description (capped at 2000 "
    "chars). Use after `search_zenodo_rag` when you need the description "
    "or license, or to confirm a citation by canonical id."
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
        description=_SEARCH_DESCRIPTION,
    )


def make_zenodo_rag_fetch_records_tool(provider: ZenodoRagProvider) -> Tool:
    """Tool factory: hydrate Zenodo records by zenodo_id or concept_recid."""

    async def fetch_zenodo_records(
        ids: list[str],
    ) -> list[dict[str, Any]]:
        """Fetch full Zenodo records by id. See tool description."""
        logger.info(
            "tool call: fetch_zenodo_records — ids=%d",
            len(ids),
        )
        record_query(
            service="zenodo.rag.fetch_records",
            query=",".join(str(i) for i in ids[:10]),
        )
        return await provider.fetch_records(ids)

    return Tool(
        fetch_zenodo_records,
        name="fetch_zenodo_records",
        description=_FETCH_RECORDS_DESCRIPTION,
    )


__all__ = [
    "make_zenodo_rag_fetch_records_tool",
    "make_zenodo_rag_search_tool",
]
