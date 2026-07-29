"""Person-discovery ORCID search tool.

Primary backend is the ORCID RAG (Qdrant) — fast semantic search over
pre-ingested EPFL/Switzerland persons, no rate limits. The live ORCID
``expanded_search`` API is used as a fallback when:

- the RAG provider is not configured,
- the RAG search returns zero hits, or
- the RAG search raises (e.g. Qdrant unavailable).

Both paths emit the same record shape so callers don't branch.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic_ai import Tool

from git_metadata_extractor.observation.query_log import record_query

if TYPE_CHECKING:
    from git_metadata_extractor.providers.base import ORCIDProvider
    from git_metadata_extractor.providers.orcid_rag import OrcidRagProvider

logger = logging.getLogger(__name__)

DEFAULT_ROWS = 50
MAX_ROWS = 200
RAG_TOP_K = 25  # we return up to `rows`, but let RAG over-fetch and trim

_DESCRIPTION = (
    "Search ORCID by free-text name (and optionally affiliation keywords) "
    "to discover candidate ORCID identifiers. Primary backend is the "
    "internal ORCID RAG index (semantic search over pre-ingested EPFL / "
    "Switzerland records, no rate limits); falls back to ORCID's expanded "
    "search API when the RAG returns no hits. "
    "Returns up to `rows` hits, each with orcid_id, given_names, "
    "family_names, credit_name, other_names, institution_names, emails, "
    "and `source` ('rag' or 'orcid_api'). "
    "Call this when you need to find an ORCID for a person whose ID is "
    "not provided in the context — then pass the chosen orcid_id to "
    "get_orcid_record for full employment/education details."
)


def _normalize_rag_hit(hit: dict[str, Any]) -> dict[str, Any]:
    """Coerce a RAG `persons` hit into the live-API result shape."""

    orcid_id = hit.get("orcid_id")
    given = hit.get("given_name")
    family = hit.get("family_name")
    display = hit.get("display_name")
    return {
        "orcid_id": orcid_id,
        "given_names": [given] if isinstance(given, str) and given else [],
        "family_names": [family] if isinstance(family, str) and family else [],
        "credit_name": display if isinstance(display, str) else None,
        "other_names": [],
        # The thin RAG persons payload omits affiliations/emails. Callers
        # that need institution disambiguation should follow up with
        # get_orcid_record(orcid_id) to fetch full employments/educations.
        "institution_names": [],
        "emails": [],
        "source": "rag",
    }


def _normalize_live_hit(hit: dict[str, Any]) -> dict[str, Any]:
    """Tag a live-API hit with `source='orcid_api'`; pass-through otherwise."""

    return {**hit, "source": "orcid_api"}


def make_query_orcid_tool(
    orcid_provider: ORCIDProvider,
    *,
    orcid_rag_provider: OrcidRagProvider | None = None,
) -> Tool:
    """Create the `query_orcid` tool.

    `orcid_rag_provider` is the primary backend; `orcid_provider` is the
    live-API fallback (always required because the RAG can be unavailable
    or stale).
    """

    async def query_orcid(
        query: str,
        rows: int = DEFAULT_ROWS,
        start: int = 0,
    ) -> list[dict[str, Any]]:
        """Search ORCID for persons by name; RAG-primary, live-API fallback.

        Args:
            query: Free-text name (e.g. "noemie mazare"); affiliation keywords
                are accepted but the boosted name fields drive ranking.
            rows: Maximum number of hits to return (1..200, default 50).
            start: Offset for pagination (default 0). Only honoured by the
                live-API fallback; the RAG path returns top hits without
                offset support.
        """

        bounded_rows = max(1, min(rows, MAX_ROWS))
        bounded_start = max(0, start)

        if orcid_rag_provider is not None and bounded_start == 0:
            try:
                logger.info(
                    "tool call: query_orcid (RAG) — query=%r rows=%d",
                    query, bounded_rows,
                )
                record_query(service="orcid.rag.search.persons", query=query)
                rag_hits = await orcid_rag_provider.search(
                    query,
                    entity_type="persons",
                    top_k=max(bounded_rows, RAG_TOP_K),
                    rerank=True,
                )
            except Exception as exc:  # noqa: BLE001 — RAG availability is optional
                logger.warning(
                    "query_orcid: RAG search failed, falling back to live API — %s",
                    exc,
                )
                rag_hits = []
            if rag_hits:
                normalized = [_normalize_rag_hit(h) for h in rag_hits[:bounded_rows]]
                return normalized

        logger.info(
            "tool call: query_orcid (live API) — query=%r rows=%d start=%d",
            query, bounded_rows, bounded_start,
        )
        record_query(service="orcid.search_persons", query=query)
        hits = orcid_provider.search_persons(
            query,
            rows=bounded_rows,
            start=bounded_start,
        )
        return [_normalize_live_hit(dict(hit)) for hit in hits]

    return Tool(
        query_orcid,
        name="query_orcid",
        description=_DESCRIPTION,
    )
