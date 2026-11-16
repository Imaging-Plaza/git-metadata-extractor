"""Generic read tools over the external sources service."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai import Tool

from git_metadata_extractor.providers.sources_service import (
    SourceSearchRequest,  # noqa: TC001 - Pydantic AI resolves tool annotations
)

if TYPE_CHECKING:
    from git_metadata_extractor.agents.tool_catalog import ToolContext


def make_sources_search_tool(context: ToolContext, _options) -> Tool:
    async def search_sources(index: str, request: SourceSearchRequest) -> dict:
        result = await context.providers.sources.search(index, request)
        return result.model_dump(mode="json", exclude_none=True)

    return Tool(
        search_sources,
        name="search_sources",
        description=(
            "Search an external source index. Use list_sources to discover store names; "
            "some stores have no search endpoint and return unsupported. Pass query, "
            "top_k and optional target/filter_payload in request. Outcomes distinguish "
            "no_matches from unavailable, unauthorized and invalid_response. "
            "Record/chunk fetch is not supported by this service version. "
            "Source snapshot or embedding metadata is included only when the server supplies it."
        ),
    )


def make_sources_manifest_tool(context: ToolContext, _options) -> Tool:
    async def list_sources() -> dict:
        result = await context.providers.sources.manifest()
        return result.model_dump(mode="json", exclude_none=True)

    return Tool(
        list_sources,
        name="list_sources",
        description="List source stores and entity types. Manifest presence does not guarantee HTTP search support.",
    )
