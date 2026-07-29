"""Pydantic-AI Tools backed by :class:`OamonitorRagProvider`.

Wraps the OAM-CH (Open Access Monitor — Switzerland) per-entity RAG
search and DuckDB hydration into pydantic-ai ``Tool`` factories. The
search tool accepts a free-text ``query`` plus an ``entity_type``
selector (``journals`` | ``publications`` | ``publishers`` |
``organisations``); the fetch tool hydrates full records from the local
DuckDB by ``_id``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic_ai import Tool

from git_metadata_extractor.observation.query_log import record_query

if TYPE_CHECKING:
    from git_metadata_extractor.providers.oamonitor_rag import OamonitorRagProvider

logger = logging.getLogger(__name__)

_SEARCH_DESCRIPTION = (
    "Semantic search over the Open Access Monitor CH (OAM-CH) index — the "
    "Swiss aggregator of journals, publications, publishers and organisations "
    "with Open Access status (gold / green / hybrid / closed) per entity. "
    "Use this to resolve a journal title to its ISSN + OA color, to look up "
    "a publication's OA status by content match, to identify the OA policy of "
    "a publisher, or to look up Swiss institutions catalogued in OAM. "
    "`entity_type` (REQUIRED) picks the collection: 'journals' (default), "
    "'publications', 'publishers', or 'organisations'. `filters`: optional "
    "allowlist dict ({entity_type, entity_id}); each value may be scalar or "
    "list. `rerank=true` engages the cross-encoder against the embedding text "
    "— useful for fine title disambiguation. Returns thin hits with "
    "entity_type, entity_id, embedding_text."
)

_FETCH_RECORDS_DESCRIPTION = (
    "Hydrate one or more OAM-CH records by their upstream `_id`. "
    "`entity_type` (REQUIRED) selects the table: 'journals' (string id), "
    "'publications' (OpenAlex URL id), 'publishers' (slug), 'organisations' "
    "(ROR URL). Returns the full raw upstream payload (`raw`) per id — use "
    "after `search_oamonitor_rag` when you need fields like ISSNs, DOI, "
    "publisher name, country code, OA color etc. that the thin search hit "
    "omits."
)


def make_oamonitor_rag_search_tool(provider: OamonitorRagProvider) -> Tool:
    """Tool factory: semantic search over the OAM-CH per-entity collections."""

    async def search_oamonitor_rag(
        query: str,
        entity_type: str = "journals",
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
        rerank: bool = False,  # noqa: FBT001, FBT002 — part of the LLM tool signature
    ) -> list[dict[str, Any]]:
        """Vector search the OAM-CH index. See tool description."""
        logger.info(
            "tool call: search_oamonitor_rag — entity=%s top_k=%d rerank=%s "
            "filters=%r query=%r",
            entity_type, top_k, rerank, filters, query,
        )
        record_query(service="oamonitor.rag.search", query=query)
        return await provider.search(
            query,
            entity_type=entity_type,
            top_k=top_k,
            filters=filters,
            rerank=rerank,
        )

    return Tool(
        search_oamonitor_rag,
        name="search_oamonitor_rag",
        description=_SEARCH_DESCRIPTION,
    )


def make_oamonitor_rag_fetch_records_tool(provider: OamonitorRagProvider) -> Tool:
    """Tool factory: hydrate OAM-CH records by `_id` for a given entity table."""

    async def fetch_oamonitor_records(
        ids: list[str],
        entity_type: str,
    ) -> list[dict[str, Any]]:
        """Fetch full OAM-CH records by id. See tool description."""
        logger.info(
            "tool call: fetch_oamonitor_records — entity=%s ids=%d",
            entity_type, len(ids),
        )
        record_query(
            service="oamonitor.rag.fetch_records",
            query=",".join(str(i) for i in ids[:10]),
        )
        return await provider.fetch_records(ids, entity_type=entity_type)

    return Tool(
        fetch_oamonitor_records,
        name="fetch_oamonitor_records",
        description=_FETCH_RECORDS_DESCRIPTION,
    )


__all__ = [
    "make_oamonitor_rag_fetch_records_tool",
    "make_oamonitor_rag_search_tool",
]
