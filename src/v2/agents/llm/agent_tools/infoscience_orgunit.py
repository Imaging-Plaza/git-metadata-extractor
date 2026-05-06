from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic_ai import Tool

from src.v2.observation.query_log import record_query

if TYPE_CHECKING:
    from src.v2.ingest.providers.base import InfoscienceProvider

logger = logging.getLogger(__name__)


def make_infoscience_orgunit_tool(infoscience_provider: InfoscienceProvider) -> Tool:
    """Create an Infoscience org-unit search tool bound to a provider instance."""

    def search_infoscience_orgunit(query: str) -> list[dict[str, Any]]:
        """Search Infoscience organization units by free-text query."""

        logger.info("tool call: search_infoscience_orgunit — query=%r", query)
        record_query(service="infoscience.search_orgunit", query=query)
        return infoscience_provider.search_orgunit(query)

    return Tool(
        search_infoscience_orgunit,
        name="search_infoscience_orgunit",
        description=(
            "Search Infoscience organization units to obtain identifiers, names, "
            "and parent organization hints."
        ),
    )
