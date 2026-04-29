from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic_ai import Tool

from src.v2.observation.query_log import record_query

if TYPE_CHECKING:
    from src.v2.ingest.providers.base import InfoscienceProvider

logger = logging.getLogger(__name__)

_DESCRIPTION = (
    "Search Infoscience (EPFL's institutional repository) for scholarly "
    "publications matching a query. Use it to find articles related to a "
    "repository, person, or organization, e.g. when CITATION.cff or README "
    "references a paper but lacks a DOI. Returns matching publications with "
    "their infosciencePublicationIdentifier, title, authors, publicationDate, "
    "doi, url, and sourceOrganization. Use the resulting "
    "infosciencePublicationIdentifier as pulse:infoscienceArticleIdentifier "
    "and the doi as schema:identifier when present."
)


def make_infoscience_publications_search_tool(
    infoscience_provider: InfoscienceProvider,
) -> Tool:
    """Create a pydantic-ai Tool that searches Infoscience for publications."""

    def search_infoscience_publications(query: str) -> list[dict[str, Any]]:
        """Search Infoscience for scholarly publications matching `query`.

        Returns a list of publication records with keys:
        infosciencePublicationIdentifier, title, authors, publicationDate,
        doi, url, sourceOrganization.
        """
        logger.info("tool call: search_infoscience_publications — query=%r", query)
        record_query(service="infoscience.search_publications", query=query)
        results = infoscience_provider.search_publications(query)
        return [
            {
                "infosciencePublicationIdentifier": record.get(
                    "infosciencePublicationIdentifier",
                ),
                "title": record.get("title"),
                "authors": record.get("authors", []),
                "publicationDate": record.get("publicationDate"),
                "doi": record.get("doi"),
                "url": record.get("url"),
                "sourceOrganization": record.get("sourceOrganization"),
            }
            for record in results
            if isinstance(record, dict)
        ]

    return Tool(
        search_infoscience_publications,
        name="search_infoscience_publications",
        description=_DESCRIPTION,
    )
