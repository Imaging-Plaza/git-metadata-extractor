from __future__ import annotations

import logging
from typing import Any, Callable

import requests
from pydantic_ai import Tool

logger = logging.getLogger(__name__)

DUCKDUCKGO_INSTANT_ANSWER_URL = "https://api.duckduckgo.com/"
DEFAULT_MAX_RESULTS = 5
MAX_RESULTS = 10
REQUEST_TIMEOUT_SECONDS = 12.0


def _to_non_empty_string(value: Any) -> str | None:
    if isinstance(value, str):
        candidate = value.strip()
        if candidate:
            return candidate
    return None


def _normalize_max_results(value: Any) -> int:
    if isinstance(value, int):
        return max(1, min(MAX_RESULTS, value))
    return DEFAULT_MAX_RESULTS


def _iter_topic_items(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not isinstance(value, list):
        return rows
    for item in value:
        if not isinstance(item, dict):
            continue
        nested = item.get("Topics")
        if isinstance(nested, list):
            rows.extend(_iter_topic_items(nested))
            continue
        rows.append(item)
    return rows


def _append_result(
    output: list[dict[str, str]],
    seen_urls: set[str],
    *,
    title: Any,
    url: Any,
    snippet: Any,
    source: str,
) -> None:
    normalized_url = _to_non_empty_string(url)
    if normalized_url is None:
        return
    url_key = normalized_url.lower()
    if url_key in seen_urls:
        return

    normalized_title = _to_non_empty_string(title)
    normalized_snippet = _to_non_empty_string(snippet)
    if normalized_title is None and normalized_snippet is None:
        normalized_title = normalized_url
        normalized_snippet = normalized_url
    elif normalized_title is None:
        normalized_title = normalized_snippet
    elif normalized_snippet is None:
        normalized_snippet = normalized_title

    output.append(
        {
            "title": normalized_title or normalized_url,
            "url": normalized_url,
            "snippet": normalized_snippet or normalized_url,
            "source": source,
        },
    )
    seen_urls.add(url_key)


def _extract_results(payload: dict[str, Any], *, max_results: int) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    _append_result(
        rows,
        seen_urls,
        title=payload.get("Heading"),
        url=payload.get("AbstractURL"),
        snippet=payload.get("AbstractText"),
        source="abstract",
    )

    results = payload.get("Results")
    if isinstance(results, list):
        for item in results:
            if not isinstance(item, dict):
                continue
            _append_result(
                rows,
                seen_urls,
                title=item.get("Text"),
                url=item.get("FirstURL"),
                snippet=item.get("Text"),
                source="results",
            )
            if len(rows) >= max_results:
                return rows[:max_results]

    for item in _iter_topic_items(payload.get("RelatedTopics")):
        _append_result(
            rows,
            seen_urls,
            title=item.get("Text"),
            url=item.get("FirstURL"),
            snippet=item.get("Text"),
            source="related_topics",
        )
        if len(rows) >= max_results:
            return rows[:max_results]

    return rows[:max_results]


def make_duckduckgo_search_tool(
    *,
    http_get: Callable[..., Any] | None = None,
) -> Tool:
    """Create a DuckDuckGo-backed internet search tool."""

    requester = http_get or requests.get

    def search_on_the_internet(query: str, max_results: int = DEFAULT_MAX_RESULTS) -> dict[str, Any]:
        """Search the internet with DuckDuckGo and return compact result context.

        Args:
            query: Search query string.
            max_results: Max result rows to return (1..10).
        """

        normalized_query = _to_non_empty_string(query) or ""
        logger.info(
            "tool call: search_on_the_internet — query=%r max_results=%r",
            normalized_query,
            max_results,
        )
        if not normalized_query:
            return {
                "query": normalized_query,
                "result_count": 0,
                "results": [],
                "error": "empty_query",
            }

        limit = _normalize_max_results(max_results)
        params = {
            "q": normalized_query,
            "format": "json",
            "no_html": "1",
            "no_redirect": "1",
            "skip_disambig": "1",
        }

        try:
            response = requester(
                DUCKDUCKGO_INSTANT_ANSWER_URL,
                params=params,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            raise_for_status = getattr(response, "raise_for_status", None)
            if callable(raise_for_status):
                raise_for_status()
            payload = response.json() if callable(getattr(response, "json", None)) else None
        except Exception as exc:  # noqa: BLE001
            return {
                "query": normalized_query,
                "result_count": 0,
                "results": [],
                "error": str(exc),
            }

        if not isinstance(payload, dict):
            return {
                "query": normalized_query,
                "result_count": 0,
                "results": [],
                "error": "invalid_duckduckgo_payload",
            }

        results = _extract_results(payload, max_results=limit)
        return {
            "query": normalized_query,
            "result_count": len(results),
            "results": results,
        }

    return Tool(
        search_on_the_internet,
        name="search_on_the_internet",
        description=(
            "Search the internet using DuckDuckGo and return compact result context "
            "(title, URL, snippet, source type). Use for quick external grounding."
        ),
    )

