"""GitHub dependents discovery.

Scrapes GitHub's public dependents page (`/network/dependents`) — there is
no official API for this — and returns a structured `DependentsResult`.

Public surface:

- `models.DependentItem`, `models.DependentsResult` — Pydantic models.
- `scraper.parse_dependents_page` — pure function from raw HTML to a parsed page.
- `scraper.iterate_dependents` — generator yielding `DependentItem` objects across pages.
- `service.list_dependents` — high-level entrypoint with caching and graceful degradation.
- `tool.make_query_dependents_tool` — pydantic-ai Tool factory for future LLM use.

This module is deliberately not wired into the main `/v2/extract` pipeline.
Use directly via `service.list_dependents(...)` or the CLI in
`scripts/v2/list_dependents.py`.
"""

from src.module.dependents.models import (
    DependentItem,
    DependentKind,
    DependentsResult,
    ParsedDependentsPage,
)

__all__ = [
    "DependentItem",
    "DependentKind",
    "DependentsResult",
    "ParsedDependentsPage",
]
