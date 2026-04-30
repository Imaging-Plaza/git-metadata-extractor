from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic_ai import Tool

from src.v2.observation.query_log import record_query

if TYPE_CHECKING:
    from src.v2.ingest.providers.base import ORCIDProvider

logger = logging.getLogger(__name__)

DEFAULT_ROWS = 50
MAX_ROWS = 200

_DESCRIPTION = (
    "Search ORCID by free-text name (and optionally affiliation keywords) "
    "to discover candidate ORCID identifiers. Wraps ORCID's expanded-search "
    "endpoint with the standard edismax boosts on given/family/credit names "
    "and on current/past institution affiliations. "
    "Returns up to `rows` hits, each with orcid_id, given_names, family_names, "
    "credit_name, other_names, institution_names, and emails. "
    "Call this tool when you need to find an ORCID for a person whose ID is "
    "not provided in the context — then pass the chosen orcid_id to "
    "get_orcid_record for full employment/education details."
)


def make_query_orcid_tool(orcid_provider: ORCIDProvider) -> Tool:
    """Create a pydantic-ai Tool that searches ORCID by name."""

    def query_orcid(
        query: str,
        rows: int = DEFAULT_ROWS,
        start: int = 0,
    ) -> list[dict[str, Any]]:
        """Search ORCID expanded-search for persons by name.

        Args:
            query: Free-text name (e.g. "noemie mazare"); affiliation keywords
                are accepted but the boosted name fields drive ranking.
            rows: Maximum number of hits to return (1..200, default 50).
            start: Offset for pagination (default 0).
        """
        logger.info(
            "tool call: query_orcid — query=%r rows=%d start=%d",
            query,
            rows,
            start,
        )
        record_query(service="orcid.search_persons", query=query)
        bounded_rows = max(1, min(rows, MAX_ROWS))
        bounded_start = max(0, start)
        hits = orcid_provider.search_persons(
            query,
            rows=bounded_rows,
            start=bounded_start,
        )
        return [dict(hit) for hit in hits]

    return Tool(
        query_orcid,
        name="query_orcid",
        description=_DESCRIPTION,
    )
