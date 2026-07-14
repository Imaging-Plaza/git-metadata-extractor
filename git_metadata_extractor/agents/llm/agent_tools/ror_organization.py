from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic_ai import Tool

from git_metadata_extractor.observation.query_log import record_query

if TYPE_CHECKING:
    from git_metadata_extractor.providers.base import RORProvider

logger = logging.getLogger(__name__)


def make_ror_organization_search_tool(ror_provider: RORProvider) -> Tool:
    """Create a ROR organization search tool bound to a provider instance."""

    def search_ror_organizations(query: str) -> list[dict[str, Any]]:
        """Search ROR organizations by free-text query."""

        logger.info("tool call: search_ror_organizations — query=%r", query)
        record_query(service="ror.search_organizations", query=query)
        return ror_provider.search_organizations(query)

    return Tool(
        search_ror_organizations,
        name="search_ror_organizations",
        description=(
            "Search ROR organizations by name or acronym and return "
            "candidate organization records."
        ),
    )
