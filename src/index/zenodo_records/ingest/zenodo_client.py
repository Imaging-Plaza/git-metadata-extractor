"""Async Zenodo REST client with rate limiting and retries.

Zenodo's documented hard limit is 30 requests / minute. We default to 25/min
via a simple monotonic-spaced semaphore, leaving headroom for retries.
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

    from src.index.zenodo_records.config import ZenodoIndexConfig

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


class ZenodoClient:
    """Async wrapper for Zenodo's `/api/records` and `/api/communities`."""

    def __init__(self, config: ZenodoIndexConfig) -> None:
        self._config = config
        self._base = config.zenodo.base_url.rstrip("/")
        self._headers: dict[str, str] = {"Accept": "application/json"}
        if config.zenodo.token:
            self._headers["Authorization"] = f"Bearer {config.zenodo.token}"
        self._limiter = _RateLimiter(config.zenodo.rate_per_minute)
        # Anonymous Zenodo callers are capped at size=25 server-side; only
        # bump to the full configured page size when we have a token.
        self._page_size = (
            config.zenodo.page_size if config.zenodo.token else min(config.zenodo.page_size, 25)
        )

    @property
    def page_size(self) -> int:
        return self._page_size

    async def _get(
        self,
        client: httpx.AsyncClient,
        path: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        @retry(
            stop=stop_after_attempt(5),
            wait=wait_exponential(multiplier=1, min=2, max=30),
            retry=retry_if_exception(_is_retryable),
            reraise=True,
        )
        async def _call() -> dict[str, Any]:
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
                        "Zenodo non-retryable %d on %s: %s",
                        response.status_code,
                        path,
                        body,
                    )
                raise
            return response.json()

        return await _call()

    async def iter_records(
        self,
        community: str,
        *,
        limit: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Page through `/records?communities=<slug>` yielding raw record dicts.

        Stops on the first empty page or when `limit` records have been yielded.
        """
        async with httpx.AsyncClient() as client:
            page = 1
            yielded = 0
            while True:
                params: dict[str, Any] = {
                    "communities": community,
                    "size": self._page_size,
                    "page": page,
                    "sort": "newest",
                }
                payload = await self._get(client, "/records", params)
                hits_block = payload.get("hits") or {}
                hits = hits_block.get("hits") or []
                if not hits:
                    return
                for hit in hits:
                    yield hit
                    yielded += 1
                    if limit is not None and yielded >= limit:
                        return
                # Zenodo caps total at 10k; once we've passed that we're done.
                if page * self._page_size >= 10_000:
                    LOGGER.warning(
                        "community %s hit Zenodo's 10k cap; remaining records "
                        "would require OAI-PMH or narrower queries",
                        community,
                    )
                    return
                page += 1

    async def fetch_community(self, slug: str) -> dict[str, Any] | None:
        try:
            async with httpx.AsyncClient() as client:
                return await self._get(client, f"/communities/{slug}", {})
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    async def fetch_record(
        self,
        record_id: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> dict[str, Any] | None:
        own_client = client is None
        if own_client:
            client = httpx.AsyncClient()
        try:
            return await self._get(client, f"/records/{record_id}", {})
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (404, 410):
                return None
            raise
        finally:
            if own_client:
                await client.aclose()
