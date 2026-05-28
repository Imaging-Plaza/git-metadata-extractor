"""Pydantic AI `Tool` factory for the dependents module.

The factory exists so the dependents lookup can be wired into an agent's
`tools=[...]` list later. Right now **no agent imports this** — the
module is available but not piped, matching the project's "build it,
don't auto-run it" stance for new analytical capabilities.

Usage when the time comes:

    from src.module.dependents.tool import make_query_dependents_tool
    tool = make_query_dependents_tool(cache=provider_cache)
    # then add `tool` to the agent's tools=[...] list

Mirrors `src/v2/agents/llm/agent_tools/query_dependencies.py` (the
forward-direction tool — what *this* repo depends on) so callers can
swap them in/out without surprises.
"""
from __future__ import annotations

import logging
from typing import Any

from pydantic_ai import Tool

from src.module.dependents.models import DependentKind
from src.module.dependents.service import (
    DEFAULT_MAX_ITEMS,
    DEFAULT_MAX_PAGES,
    list_dependents,
)
from src.v2.ingest.cache import ProviderCache
from src.v2.observation.query_log import record_query

logger = logging.getLogger(__name__)

_DESCRIPTION = (
    "Discover repositories or packages that *depend on* a given GitHub "
    "repository, scraped from the public `/network/dependents` page. "
    "Returns up to N top dependents shaped as "
    "`{full_name, owner, repo, stars, forks}` plus the total dependents count "
    "GitHub reports. "
    "Use `kind='REPOSITORY'` (default) to list dependent repos, or "
    "`kind='PACKAGE'` for dependent registry packages. "
    "`max_pages` and `max_items` cap the scrape depth (default 5 / 100). "
    "Call this when reverse-dependency information would meaningfully "
    "inform your assessment — e.g. estimating ecosystem reach, gauging a "
    "library's importance, or identifying integrations. Skip when the "
    "README/metadata is already sufficient: each call hits a Selenium "
    "browser session and is not free. "
    "Returns `available=False` with explanatory warnings when GitHub has "
    "the dependency graph disabled for the repository, when Selenium is "
    "unavailable, or when the URL is malformed."
)


def make_query_dependents_tool(
    *,
    cache: ProviderCache | None = None,
) -> Tool:
    """Create a pydantic-ai Tool that fetches dependents for a repository.

    The tool's signature is intentionally narrow: it returns the
    structured `DependentsResult` as a JSON-serialisable dict, so the
    LLM sees a stable shape regardless of the underlying scraper's
    behaviour. Failures degrade to `available=False` rather than
    raising.
    """

    def query_dependents(
        full_name: str,
        kind: DependentKind = "REPOSITORY",
        max_pages: int = DEFAULT_MAX_PAGES,
        max_items: int = DEFAULT_MAX_ITEMS,
    ) -> dict[str, Any]:
        """Return structured dependents info for a repository.

        Args:
            full_name: GitHub repository handle in ``owner/repo`` form.
            kind: ``"REPOSITORY"`` (default) for dependent repos, or
                ``"PACKAGE"`` for dependent registry packages.
            max_pages: Maximum number of dependents pages to walk (default 5).
            max_items: Maximum number of items to return (default 100).
        """

        logger.info(
            "tool call: query_dependents — full_name=%r kind=%r "
            "max_pages=%d max_items=%d",
            full_name,
            kind,
            max_pages,
            max_items,
        )
        record_query(service="github.dependents", query=f"{full_name}|{kind}")
        result = list_dependents(
            full_name,
            kind=kind,
            max_pages=max_pages,
            max_items=max_items,
            cache=cache,
        )
        return result.model_dump(mode="json")

    return Tool(
        query_dependents,
        name="query_dependents",
        description=_DESCRIPTION,
    )


__all__ = ["make_query_dependents_tool"]
