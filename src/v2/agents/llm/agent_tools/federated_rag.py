"""Pydantic-AI tools backed by :class:`FederatedRagProvider`.

Two tools — `search_federated_rag` and `lookup_entity_federated` — that
fan out across every registered gme RAG index in parallel and return a
single merged response. Currently registered: ``huggingface``,
``openalex``, ``infoscience``, ``ethz_research_collection``, ``orcid``,
``ror``, ``zenodo``, ``snsf``, ``swissubase``, ``renkulab``, ``github``,
``epfl_graph``. Use these instead of (or in addition to) the per-index
tools when you don't know which index has the answer.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic_ai import Tool

from src.v2.observation.query_log import record_query

if TYPE_CHECKING:
    from src.v2.ingest.providers.federated_rag import FederatedRagProvider

logger = logging.getLogger(__name__)


_SEARCH_DESCRIPTION = (
    "Federated semantic search across every registered gme RAG index "
    "(huggingface, openalex, infoscience, ethz_research_collection, orcid, "
    "ror, zenodo, snsf, swissubase, renkulab, github, epfl_graph) in "
    "parallel. Use when the query could match in multiple indices and you "
    "want one merged ranked list, e.g. 'find Swiss German LLM resources' "
    "returns HF models + Zenodo datasets + ORCID researchers + ROR "
    "institutions + EPFL/ETHZ publications side by side. "
    "`indices`: optional subset (e.g. ['ethz_research_collection','infoscience']); "
    "default = all. `entity_type`: optional restriction within each index "
    "(e.g. 'models', 'works', 'persons', 'chunks', 'articles'); adapters "
    "that don't recognise the type fall back to their default. "
    "`top_k_per_index`: how many candidates each index contributes "
    "(default 3). `top_k`: overall hits returned after merge and sort by "
    "score (default 10). `filters`: dict forwarded as-is to every adapter; "
    "each adapter applies the keys it knows and ignores the rest. Returns "
    "`{hits: [{index,entity_type,id,title,score,summary,url,payload}], "
    "by_index: {…}, errors: {…}}`. Always prefer this over running "
    "per-index tools serially."
)

_LOOKUP_DESCRIPTION = (
    "Cross-index entity lookup: given an identifier (HF slug or repo_id, "
    "OpenAlex Wxxxx/Axxxx/Ixxxx ID, ORCID 0000-..., ROR id, Zenodo numeric "
    "id or DOI, Infoscience UUID, ETH Research Collection handle "
    "(20.500.11850/...) or research-collection.ethz.ch URL, GitHub "
    "owner/repo, or any of the corresponding canonical URLs), every "
    "adapter that recognises the shape resolves it and returns 0+ matching "
    "records. Use to hydrate a known identifier into all related records "
    "the gme corpus has, e.g. an ORCID → ORCID person record + (if the "
    "same person published HF models / EPFL or ETHZ papers under the same "
    "name) HF / Infoscience / ETH RC entries. `indices`: optional subset; "
    "default = all. Returns `{identifier, records: "
    "[{index,entity_type,id,data,url}], by_index: {…}, errors: {…}}`."
)


def make_federated_rag_search_tool(provider: FederatedRagProvider) -> Tool:
    """Tool factory: federated semantic search across all registered indices."""

    async def search_federated_rag(  # noqa: PLR0913 — full federated knob set surfaced to the LLM
        query: str,
        indices: list[str] | None = None,
        entity_type: str | None = None,
        top_k: int = 10,
        top_k_per_index: int = 3,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Federated semantic search. See tool description."""
        logger.info(
            "tool call: search_federated_rag — indices=%s entity_type=%s "
            "top_k=%d top_k_per_index=%d filters=%r query=%r",
            indices, entity_type, top_k, top_k_per_index, filters, query,
        )
        record_query(service="federated.rag.search", query=query)
        return await provider.search(
            query,
            indices=indices,
            entity_type=entity_type,
            top_k=top_k,
            top_k_per_index=top_k_per_index,
            filters=filters,
        )

    return Tool(
        search_federated_rag,
        name="search_federated_rag",
        description=_SEARCH_DESCRIPTION,
    )


def make_federated_rag_lookup_tool(provider: FederatedRagProvider) -> Tool:
    """Tool factory: cross-index entity lookup by identifier."""

    async def lookup_entity_federated(
        identifier: str,
        indices: list[str] | None = None,
    ) -> dict[str, Any]:
        """Cross-index identifier resolution. See tool description."""
        logger.info(
            "tool call: lookup_entity_federated — indices=%s identifier=%r",
            indices, identifier,
        )
        record_query(service="federated.rag.lookup", query=identifier)
        return await provider.lookup(identifier, indices=indices)

    return Tool(
        lookup_entity_federated,
        name="lookup_entity_federated",
        description=_LOOKUP_DESCRIPTION,
    )


__all__ = [
    "make_federated_rag_lookup_tool",
    "make_federated_rag_search_tool",
]
