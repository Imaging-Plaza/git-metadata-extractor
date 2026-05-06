"""Async RenkuLab REST client with rate limiting and retries.

Targets the public data API at `https://renkulab.io/api/data`. Three of
the listing endpoints (`/projects`, `/groups`, `/data_connectors`) are
unauthenticated and use header-based pagination (`page`, `total-pages`,
`per-page` response headers). The `/users` listing requires auth, so we
harvest users via the public `/search/query?q=type:User` endpoint instead.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from src.index.renkulab.config import RenkulabIndexConfig

LOGGER = logging.getLogger(__name__)

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS
    return False


class _RateLimiter:
    """Simple monotonic-spaced async limiter: at most N events per minute."""

    def __init__(self, per_minute: int) -> None:
        if per_minute <= 0:
            message = "rate_per_minute must be positive"
            raise ValueError(message)
        self._min_interval = 60.0 / per_minute
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._min_interval - (now - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()


class RenkulabClient:
    """Async wrapper for selected RenkuLab data API endpoints."""

    def __init__(self, config: RenkulabIndexConfig) -> None:
        self._config = config
        self._base = config.renkulab.base_url.rstrip("/")
        self._headers: dict[str, str] = {"Accept": "application/json"}
        if config.renkulab.token:
            self._headers["Authorization"] = f"Bearer {config.renkulab.token}"
        self._limiter = _RateLimiter(config.renkulab.rate_per_minute)
        self._page_size = config.renkulab.page_size

    @property
    def page_size(self) -> int:
        return self._page_size

    async def _get(
        self,
        client: httpx.AsyncClient,
        path: str,
        params: dict[str, Any],
    ) -> httpx.Response:
        @retry(
            stop=stop_after_attempt(5),
            wait=wait_exponential(multiplier=1, min=2, max=30),
            retry=retry_if_exception(_is_retryable),
            reraise=True,
        )
        async def _call() -> httpx.Response:
            await self._limiter.acquire()
            response = await client.get(
                f"{self._base}{path}",
                params=params,
                headers=self._headers,
                timeout=httpx.Timeout(60.0),
                follow_redirects=True,
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if not _is_retryable(exc):
                    body = response.text[:500]
                    LOGGER.error(
                        "Renku non-retryable %d on %s: %s",
                        response.status_code,
                        path,
                        body,
                    )
                raise
            return response

        return await _call()

    async def _iter_paged(
        self,
        path: str,
        *,
        extra_params: dict[str, Any] | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Header-paginated list endpoints (`/projects`, `/groups`, `/data_connectors`)."""
        async with httpx.AsyncClient() as client:
            page = 1
            yielded = 0
            while True:
                params: dict[str, Any] = {
                    "per_page": self._page_size,
                    "page": page,
                }
                if extra_params:
                    params.update(extra_params)
                response = await self._get(client, path, params)
                items = response.json() or []
                if not items:
                    return
                for item in items:
                    yield item
                    yielded += 1
                    if limit is not None and yielded >= limit:
                        return
                total_pages_hdr = response.headers.get("total-pages")
                try:
                    total_pages = int(total_pages_hdr) if total_pages_hdr else None
                except ValueError:
                    total_pages = None
                if total_pages is not None and page >= total_pages:
                    return
                page += 1

    def iter_projects(
        self,
        *,
        limit: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        return self._iter_paged("/projects", limit=limit)

    def iter_groups(
        self,
        *,
        limit: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        return self._iter_paged("/groups", limit=limit)

    def iter_data_connectors(
        self,
        *,
        limit: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        return self._iter_paged("/data_connectors", limit=limit)

    async def iter_search(
        self,
        query: str,
        *,
        limit: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """`/search/query` uses an envelope (`items`, `pagingInfo`) instead of headers."""
        async with httpx.AsyncClient() as client:
            page = 1
            yielded = 0
            while True:
                params = {
                    "q": query,
                    "per_page": self._page_size,
                    "page": page,
                }
                response = await self._get(client, "/search/query", params)
                payload = response.json() or {}
                items = payload.get("items") or []
                if not items:
                    return
                for item in items:
                    yield item
                    yielded += 1
                    if limit is not None and yielded >= limit:
                        return
                paging = payload.get("pagingInfo") or {}
                total_pages = paging.get("totalPages")
                if total_pages is not None and page >= int(total_pages):
                    return
                page += 1

    async def fetch_group_members(self, slug: str) -> list[dict[str, Any]]:
        async with httpx.AsyncClient() as client:
            try:
                response = await self._get(
                    client,
                    f"/groups/{slug}/members",
                    {},
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (401, 403, 404):
                    return []
                raise
        body = response.json() or []
        return list(body) if isinstance(body, list) else []

    async def fetch_project_members(self, project_id: str) -> list[dict[str, Any]]:
        async with httpx.AsyncClient() as client:
            try:
                response = await self._get(
                    client,
                    f"/projects/{project_id}/members",
                    {},
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (401, 403, 404):
                    return []
                raise
        body = response.json() or []
        return list(body) if isinstance(body, list) else []
