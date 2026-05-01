"""Pydantic-AI Tools backed by :class:`InfoscienceRagProvider`.

Three tools so the LLM can drive a RAG search/fetch loop on demand:

* ``search_infoscience_rag`` — semantic vector search across the
  Infoscience index (chunks / articles / persons / organizations) with an
  allowlisted filter dict and an opt-in cross-encoder rerank pass.
* ``fetch_infoscience_chunks`` — pulls full chunk bodies for a single
  article identified by ``article_uuid`` (returned by the search tool).
* ``fetch_infoscience_records`` — full payloads for articles / persons /
  organizations by point ID.

Splitting search from fetch keeps prompts small: search returns thin hits
(IDs + titles + short snippets), and the agent decides when to spend the
context budget on a full body.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic_ai import Tool

from src.v2.ingest.providers.infoscience_rag import (
    CollectionName,  # noqa: TC001 — runtime annotation read by pydantic-ai
)
from src.v2.observation.query_log import record_query

if TYPE_CHECKING:
    from src.v2.ingest.providers.infoscience_rag import InfoscienceRagProvider

logger = logging.getLogger(__name__)

_SEARCH_DESCRIPTION = (
    "Semantic search over the Infoscience RAG index. Use this when README / "
    "CITATION / metadata mentions a paper, lab, or person without a clean "
    "identifier and you need to find the matching record. "
    "`collection`: 'chunks' (default — paper body fragments), 'articles' "
    "(one row per paper), 'persons' (researchers), or 'organizations' (EPFL "
    "labs/units). `filters`: optional dict of allowlisted keys "
    "(has_github_match, has_hf_match, year, publication_type, language, "
    "lab_uuid, org_uuids, doi, orcid, ror_id, sciper_id, sciper_unit_id, "
    "author_uuids, subjects, keywords). Each value may be a scalar, a list "
    "(any-of), or an operator dict ($eq/$ne/$in/$contains/$gte/$lte). "
    "`rerank=true` engages the cross-encoder for ambiguous queries — slower "
    "but more precise; default false. Returns trimmed hits (id, score, "
    "title/name, snippet, identifiers). Feed the returned id into "
    "fetch_infoscience_chunks (article_uuid) or fetch_infoscience_records "
    "to pull the full body when needed."
)

_FETCH_CHUNKS_DESCRIPTION = (
    "Fetch the full chunk bodies of one Infoscience article by its "
    "`article_uuid` (returned from search_infoscience_rag). Use this only "
    "after a search hit looks promising — it spends meaningful context "
    "budget. Returns chunks ordered by chunk_index with their full text, "
    "title, doi, and infoscience_url."
)

_FETCH_RECORDS_DESCRIPTION = (
    "Fetch full payloads of articles, persons, or organizations by point "
    "id (the `id` field returned by search_infoscience_rag). `collection` "
    "must be 'articles', 'persons', or 'organizations' — chunk bodies use "
    "fetch_infoscience_chunks instead. Returns the complete payload for "
    "each id (abstract, affiliations, parent org chain, etc.)."
)


def _service_name(op: str) -> str:
    return f"infoscience.rag.{op}"


def make_infoscience_rag_search_tool(provider: InfoscienceRagProvider) -> Tool:
    """Tool factory: semantic search over the Infoscience RAG index."""

    async def search_infoscience_rag(
        query: str,
        collection: CollectionName = "chunks",
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
        rerank: bool = False,  # noqa: FBT001, FBT002 — part of the LLM tool signature
    ) -> list[dict[str, Any]]:
        """Vector search the Infoscience index. See tool description."""
        logger.info(
            "tool call: search_infoscience_rag — collection=%s top_k=%d "
            "rerank=%s filters=%r query=%r",
            collection, top_k, rerank, filters, query,
        )
        record_query(
            service=f"{_service_name('search')}.{collection}",
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
        search_infoscience_rag,
        name="search_infoscience_rag",
        description=_SEARCH_DESCRIPTION,
    )


def make_infoscience_rag_fetch_chunks_tool(
    provider: InfoscienceRagProvider,
) -> Tool:
    """Tool factory: fetch full chunk bodies for one article."""

    async def fetch_infoscience_chunks(
        article_uuid: str,
        max_chunks: int = 20,
    ) -> list[dict[str, Any]]:
        logger.info(
            "tool call: fetch_infoscience_chunks — article_uuid=%s max_chunks=%d",
            article_uuid, max_chunks,
        )
        record_query(
            service=_service_name("fetch_chunks"),
            query=article_uuid,
        )
        return await provider.fetch_chunks(article_uuid, max_chunks=max_chunks)

    return Tool(
        fetch_infoscience_chunks,
        name="fetch_infoscience_chunks",
        description=_FETCH_CHUNKS_DESCRIPTION,
    )


def make_infoscience_rag_fetch_records_tool(
    provider: InfoscienceRagProvider,
) -> Tool:
    """Tool factory: fetch full article/person/organization payloads by id."""

    async def fetch_infoscience_records(
        collection: CollectionName,
        ids: list[str],
    ) -> list[dict[str, Any]]:
        logger.info(
            "tool call: fetch_infoscience_records — collection=%s ids=%d",
            collection, len(ids),
        )
        record_query(
            service=f"{_service_name('fetch_records')}.{collection}",
            query=",".join(str(i) for i in ids),
        )
        try:
            return await provider.fetch_records(collection, ids)
        except ValueError as exc:
            logger.warning("fetch_infoscience_records: %s", exc)
            return []

    return Tool(
        fetch_infoscience_records,
        name="fetch_infoscience_records",
        description=_FETCH_RECORDS_DESCRIPTION,
    )


__all__ = [
    "make_infoscience_rag_fetch_chunks_tool",
    "make_infoscience_rag_fetch_records_tool",
    "make_infoscience_rag_search_tool",
]
