"""High-level entrypoint: `list_dependents(full_name, ...)`.

Wraps the scraper with caching (standard `ProviderCache`, 30-day TTL by
default) and graceful degradation.

Caching strategy: we cache the *aggregated* `DependentsResult` keyed on
`(full_name, kind, max_pages, max_items)`. On a cache hit, one DB lookup
returns the full result. On a cache miss, the scraper walks pages
serially via Selenium. Per-page caching is intentionally not added — it
would make invalidation more complex without meaningful speedup
(individual page fetches are not reused across different repos).

Empty / failed lookups are returned as `DependentsResult(available=False,
warnings=[...])` rather than raised exceptions, so callers can handle
"no dependents available" uniformly.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable

from src.module.dependents.models import (
    DependentItem,
    DependentKind,
    DependentsResult,
)
from src.module.dependents.scraper import (
    DEFAULT_PAGE_TIMEOUT_SECONDS,
    DEFAULT_PAGE_WAIT_SECONDS,
    fetch_dependents_html,
    iterate_dependents,
)
from src.v2.ingest.cache import ProviderCache

logger = logging.getLogger(__name__)

DEFAULT_MAX_PAGES = 5
DEFAULT_MAX_ITEMS = 100


def _coerce_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    return value if value > 0 else default


def _resolve_caps(
    max_pages: int | None,
    max_items: int | None,
) -> tuple[int, int]:
    """Resolve user-supplied caps, falling back to env vars then defaults."""

    pages = max_pages if isinstance(max_pages, int) and max_pages > 0 else None
    items = max_items if isinstance(max_items, int) and max_items > 0 else None
    if pages is None:
        pages = _coerce_int_env("V2_DEPENDENTS_MAX_PAGES", DEFAULT_MAX_PAGES)
    if items is None:
        items = _coerce_int_env("V2_DEPENDENTS_MAX_ITEMS", DEFAULT_MAX_ITEMS)
    return pages, items


def _selenium_available() -> bool:
    return bool(os.getenv("SELENIUM_REMOTE_URL", "").strip())


def list_dependents(
    full_name: str,
    *,
    kind: DependentKind = "REPOSITORY",
    max_pages: int | None = None,
    max_items: int | None = None,
    cache: ProviderCache | None = None,
    fetcher: Callable[[str], str] | None = None,
) -> DependentsResult:
    """Look up dependents for `<owner>/<repo>` and return a structured result.

    `cache` — optional `ProviderCache`. When provided, the aggregated
    result is cached with the standard provider-cache TTL.

    `fetcher` — optional alternative HTML fetcher (`url -> html`). Used by
    tests to bypass Selenium. Defaults to `fetch_dependents_html`.

    Never raises on remote failures: returns a degraded result with
    `available=False` and a populated `warnings` list instead.
    """

    if "/" not in full_name:
        return DependentsResult(
            full_name=full_name,
            kind=kind,
            available=False,
            warnings=[
                f"Invalid full_name '{full_name}': expected 'owner/repo'.",
            ],
        )

    pages_cap, items_cap = _resolve_caps(max_pages, max_items)

    cache_key: str | None = None
    if cache is not None:
        cache_key = ProviderCache.make_key(
            "github_dependents",
            "list_dependents",
            full_name=full_name,
            kind=kind,
            max_pages=pages_cap,
            max_items=items_cap,
        )
        cached = cache.get(cache_key)
        if cached is not None:
            logger.info(
                "github_dependents cache hit: %s kind=%s",
                full_name,
                kind,
            )
            try:
                return DependentsResult.model_validate(cached)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "github_dependents cached payload invalid for %s — refetching: %s",
                    full_name,
                    exc,
                )

    if fetcher is None and not _selenium_available():
        return DependentsResult(
            full_name=full_name,
            kind=kind,
            available=False,
            warnings=[
                "SELENIUM_REMOTE_URL is not set — cannot scrape dependents page. "
                "Set the env var or pass an explicit `fetcher` to enable lookups.",
            ],
        )

    items: list[DependentItem] = []
    pages_visited = 0
    total_count = 0
    warnings: list[str] = []
    seen_keys: set[tuple[str, str]] = set()

    # We walk pages by manually invoking the iterator's pieces so we
    # capture per-page metadata (counts, selected_kind) along the way.
    if fetcher is None:
        page_fetcher = fetch_dependents_html
    else:
        page_fetcher = fetcher

    try:
        for item, parsed in iterate_dependents(
            full_name,
            kind=kind,
            max_pages=pages_cap,
            max_items=items_cap,
            fetcher=_streaming_fetcher(page_fetcher, full_name, kind, pages_cap),
        ):
            total_count = (
                parsed.repository_count if kind == "REPOSITORY"
                else parsed.package_count
            )
            key = (item.owner.lower(), item.repo.lower())
            if key in seen_keys:
                # Pagination duplicates — defensive.
                continue
            seen_keys.add(key)
            items.append(item)
    except Exception as exc:  # noqa: BLE001
        logger.exception("github_dependents: unhandled error for %s", full_name)
        warnings.append(f"Unhandled scraper error: {exc}")

    truncated = items_cap <= len(items) and total_count > len(items)
    available = bool(items) or total_count == 0  # 0 deps is a valid 'available' result
    if not items and total_count == 0 and not warnings:
        # No degradation, just a real "this repo has no dependents".
        warnings.append("Repository reports zero dependents.")

    result = DependentsResult(
        full_name=full_name,
        kind=kind,
        total_count=total_count,
        fetched_count=len(items),
        truncated=truncated,
        items=items,
        available=available,
        pages_fetched=pages_visited or _approx_pages(len(items)),
        warnings=warnings,
    )

    if cache is not None and cache_key is not None and result.available:
        cache.set(cache_key, result.model_dump(mode="json"))

    return result


def _streaming_fetcher(
    fetcher: Callable[[str], str],
    full_name: str,
    kind: DependentKind,
    pages_cap: int,
):
    """Adapt a `(url) -> html` callable to the iterator's `Iterable[str]` shape."""

    from src.module.dependents.scraper import build_dependents_url, parse_dependents_page

    def _gen():
        url: str | None = build_dependents_url(full_name, kind=kind)
        page = 0
        while url and page < pages_cap:
            html = fetcher(url) if callable(fetcher) else ""
            page += 1
            yield html
            if not html:
                return
            parsed = parse_dependents_page(html)
            url = parsed.next_cursor_url

    return _gen()


def _approx_pages(items_count: int) -> int:
    """Rough page count when we don't track it exactly. 30 items per page."""

    if items_count <= 0:
        return 0
    return (items_count + 29) // 30


__all__ = [
    "DEFAULT_MAX_ITEMS",
    "DEFAULT_MAX_PAGES",
    "list_dependents",
]
