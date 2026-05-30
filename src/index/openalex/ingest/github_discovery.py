"""Discover Works that mention GitHub URLs in their text.

Two OpenAlex search filters are used:

- `fulltext.search` — searches the full text of works where OpenAlex has it.
- `default.search` — searches title + abstract.

Use `--search both` to union the result sets (deduped on `openalex_id`).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Literal

from src.index.openalex.ingest.openalex_client import batched, iter_works
from src.index.openalex.ingest.works import persist_work

if TYPE_CHECKING:
    from collections.abc import Iterator

    from src.index.openalex.config import OpenAlexIndexConfig
    from src.index.openalex.storage.duckdb_store import OpenAlexStore

LOGGER = logging.getLogger(__name__)

SearchMode = Literal["fulltext", "default", "both"]
GITHUB_TERM = "github.com"


def _fulltext_filter(scope_filter: dict[str, Any], term: str) -> dict[str, Any]:
    return {**scope_filter, "fulltext": {"search": term}}


def _iter_search(
    *,
    config: OpenAlexIndexConfig,
    scope_filter: dict[str, Any],
    mode: SearchMode,
    term: str,
    limit: int | None,
) -> Iterator[dict[str, Any]]:
    if mode in ("fulltext", "both"):
        yield from iter_works(
            config=config,
            filters=_fulltext_filter(scope_filter, term),
            limit=limit,
        )
    if mode in ("default", "both"):
        yield from iter_works(
            config=config,
            filters=scope_filter,
            extra_search=term,
            limit=limit,
        )


def discover_github_works(
    *,
    config: OpenAlexIndexConfig,
    store: OpenAlexStore,
    scope_filter: dict[str, Any],
    mode: SearchMode = "both",
    term: str = GITHUB_TERM,
    limit: int | None = None,
) -> tuple[int, int]:
    """Run search(es), upsert each Work, return (seen, persisted) counts.

    Dedupes within a single run on `openalex_id` so `mode="both"` doesn't
    double-count when the same work appears in both result sets.
    """
    seen = 0
    persisted = 0
    last_logged = 0
    deduped: set[str] = set()
    items = _iter_search(
        config=config,
        scope_filter=scope_filter,
        mode=mode,
        term=term,
        limit=limit,
    )
    for batch in batched(items, config.openalex.per_page):
        with store.transaction():
            for item in batch:
                seen += 1
                work_id = item.get("id")
                if not work_id or work_id in deduped:
                    continue
                deduped.add(work_id)
                if persist_work(store, item):
                    persisted += 1
        if persisted - last_logged >= 200:
            LOGGER.info("github-discovery: persisted %d works", persisted)
            last_logged = persisted
    LOGGER.info(
        "github-discovery complete: seen=%d persisted=%d (mode=%s)",
        seen,
        persisted,
        mode,
    )
    return seen, persisted
