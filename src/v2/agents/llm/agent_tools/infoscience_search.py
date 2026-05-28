from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic_ai import Tool

from src.v2.observation.query_log import record_query

if TYPE_CHECKING:
    from src.v2.ingest.providers.base import InfoscienceProvider

logger = logging.getLogger(__name__)

_DESCRIPTION = (
    "Search Infoscience for person records by name or query string. "
    "Returns matching persons with their infosciencePersonIdentifier, ORCID, "
    "affiliations, profileUrl, and relevance score. "
    "Use the highest-scoring result's infosciencePersonIdentifier for "
    "pulse:infosciencePersonIdentifier. "
    "Call this tool before assigning pulse:infosciencePersonIdentifier."
)


def make_infoscience_search_tool(infoscience_provider: InfoscienceProvider) -> Tool:
    """Create a pydantic-ai Tool that searches Infoscience for person records.

    Returns a Tool whose closure captures ``infoscience_provider`` so it can
    be called by the LLM agent at runtime without any additional context.
    """

    def search_infoscience_person(query: str) -> list[dict[str, Any]]:
        """Search Infoscience for person records by name or query string.

        Returns a list of person records. Each record contains:
        infosciencePersonIdentifier, name, orcid, affiliations, profileUrl, score.
        Pick the highest-score result for pulse:infosciencePersonIdentifier.
        """
        logger.info("tool call: search_infoscience_person — query=%r", query)
        record_query(service="infoscience.search_person", query=query)
        results = infoscience_provider.search_person(query)
        return [
            {
                "infosciencePersonIdentifier": record.get("infosciencePersonIdentifier"),
                "name": record.get("name"),
                "orcid": record.get("orcid"),
                "affiliations": record.get("affiliations", []),
                "profileUrl": record.get("profileUrl"),
                "score": record.get("score"),
            }
            for record in results
            if isinstance(record, dict)
        ]

    return Tool(
        search_infoscience_person,
        name="search_infoscience_person",
        description=_DESCRIPTION,
    )
