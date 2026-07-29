"""Pydantic-AI Tools backed by :class:`EthzResearchCollectionRagProvider`.

Three tools so the LLM can drive a RAG search/fetch loop on demand:

* ``search_ethz_research_collection_rag`` — semantic vector search across the
  ETH Research Collection index (chunks / articles / persons / organizations) with an
  allowlisted filter dict and an opt-in cross-encoder rerank pass.
* ``fetch_ethz_research_collection_chunks`` — pulls full chunk bodies for a single
  article identified by ``article_uuid`` (returned by the search tool).
* ``fetch_ethz_research_collection_records`` — full payloads for articles / persons /
  organizations by point ID.

Splitting search from fetch keeps prompts small: search returns thin hits
(IDs + titles + short snippets), and the agent decides when to spend the
context budget on a full body.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic_ai import Tool

from git_metadata_extractor.providers.ethz_research_collection_rag import (
    CollectionName,  # noqa: TC001 — runtime annotation read by pydantic-ai
)
from git_metadata_extractor.observation.query_log import record_query

if TYPE_CHECKING:
    from git_metadata_extractor.providers.ethz_research_collection_rag import (
        EthzResearchCollectionRagProvider,
    )

logger = logging.getLogger(__name__)

_SEARCH_DESCRIPTION = (
    "Semantic search over the ETH Research Collection RAG index — ETH "
    "Zurich's institutional repository (DSpace-backed, sister index to "
    "EPFL's Infoscience). Use this when README / CITATION / metadata "
    "references an ETHZ paper, lab, or person without a clean identifier. "
    "`collection`: 'chunks' (default — paper body fragments), 'articles' "
    "(one row per paper), 'persons' (researchers), or 'organizations' "
    "(ETHZ labs/units). `filters`: optional dict of allowlisted keys "
    "(has_github_match, has_hf_match, year, publication_type, language, "
    "lab_uuid, org_uuids, doi, orcid, ror_id, author_uuids, subjects, "
    "keywords). Each value may be a scalar, a list (any-of), or an "
    "operator dict ($eq/$ne/$in/$contains/$gte/$lte). `rerank=true` "
    "engages the cross-encoder for ambiguous queries — slower but more "
    "precise; default false. Returns trimmed hits (id, score, "
    "title/name, snippet, identifiers). Feed the returned id into "
    "fetch_ethz_research_collection_chunks (article_uuid) or "
    "fetch_ethz_research_collection_records to pull the full body."
)

_FETCH_CHUNKS_DESCRIPTION = (
    "Fetch the full chunk bodies of one ETH Research Collection article "
    "by its `article_uuid` (returned from "
    "search_ethz_research_collection_rag). Use this only after a search "
    "hit looks promising — it spends meaningful context budget. Returns "
    "chunks ordered by chunk_index with their full text, title, doi, "
    "and research_collection_url."
)

_FETCH_RECORDS_DESCRIPTION = (
    "Fetch full payloads of articles, persons, or organizations by point "
    "id (the `id` field returned by search_ethz_research_collection_rag). `collection` "
    "must be 'articles', 'persons', or 'organizations' — chunk bodies use "
    "fetch_ethz_research_collection_chunks instead. Returns the complete payload for "
    "each id (abstract, affiliations, parent org chain, etc.)."
)


def _service_name(op: str) -> str:
    return f"ethz_research_collection.rag.{op}"


def make_ethz_research_collection_rag_search_tool(provider: EthzResearchCollectionRagProvider) -> Tool:
    """Tool factory: semantic search over the ETH Research Collection RAG index."""

    async def search_ethz_research_collection_rag(
        query: str,
        collection: CollectionName = "chunks",
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
        rerank: bool = False,  # noqa: FBT001, FBT002 — part of the LLM tool signature
    ) -> list[dict[str, Any]]:
        """Vector search the ETH Research Collection index. See tool description."""
        logger.info(
            "tool call: search_ethz_research_collection_rag — collection=%s top_k=%d "
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
        search_ethz_research_collection_rag,
        name="search_ethz_research_collection_rag",
        description=_SEARCH_DESCRIPTION,
    )


def make_ethz_research_collection_rag_fetch_chunks_tool(
    provider: EthzResearchCollectionRagProvider,
) -> Tool:
    """Tool factory: fetch full chunk bodies for one article."""

    async def fetch_ethz_research_collection_chunks(
        article_uuid: str,
        max_chunks: int = 20,
    ) -> list[dict[str, Any]]:
        logger.info(
            "tool call: fetch_ethz_research_collection_chunks — article_uuid=%s max_chunks=%d",
            article_uuid, max_chunks,
        )
        record_query(
            service=_service_name("fetch_chunks"),
            query=article_uuid,
        )
        return await provider.fetch_chunks(article_uuid, max_chunks=max_chunks)

    return Tool(
        fetch_ethz_research_collection_chunks,
        name="fetch_ethz_research_collection_chunks",
        description=_FETCH_CHUNKS_DESCRIPTION,
    )


def make_ethz_research_collection_rag_fetch_records_tool(
    provider: EthzResearchCollectionRagProvider,
) -> Tool:
    """Tool factory: fetch full article/person/organization payloads by id."""

    async def fetch_ethz_research_collection_records(
        collection: CollectionName,
        ids: list[str],
    ) -> list[dict[str, Any]]:
        logger.info(
            "tool call: fetch_ethz_research_collection_records — collection=%s ids=%d",
            collection, len(ids),
        )
        record_query(
            service=f"{_service_name('fetch_records')}.{collection}",
            query=",".join(str(i) for i in ids),
        )
        try:
            return await provider.fetch_records(collection, ids)
        except ValueError as exc:
            logger.warning("fetch_ethz_research_collection_records: %s", exc)
            return []

    return Tool(
        fetch_ethz_research_collection_records,
        name="fetch_ethz_research_collection_records",
        description=_FETCH_RECORDS_DESCRIPTION,
    )


__all__ = [
    "make_ethz_research_collection_rag_fetch_chunks_tool",
    "make_ethz_research_collection_rag_fetch_records_tool",
    "make_ethz_research_collection_rag_search_tool",
]
