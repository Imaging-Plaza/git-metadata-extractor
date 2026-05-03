"""Async DSpace 7 REST client for the ETH Research Collection.

Single `httpx.AsyncClient` that talks to `https://www.research-collection.ethz.ch/server/api`
with optional bearer-token auth. Covers exactly the endpoints we need:

    * `/discover/search/objects` — Solr-backed search, used for the
      `fulltext:` qualifier in `discover.py`.
    * `/core/items/{uuid}` — item metadata, used for Articles, Persons,
      and Organizations alike.
    * `/core/items/{uuid}/bundles` + `/core/bundles/{uuid}/bitstreams` —
      bundle/bitstream listing for the TEXT bundle.
    * `/core/bitstreams/{uuid}/content` — the plain-text body for matched
      publications (no PDFs handled on our side).

dspace-rest-client was evaluated and dropped: its auth model (session +
CSRF + JWT login) conflicts with the ETH Research Collection's bearer-token auth.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from .config import EthzResearchCollectionConfig

logger = logging.getLogger(__name__)


class DSpaceError(Exception):
    """Raised when a DSpace REST call fails after retries."""


class DSpaceClient:
    """Async DSpace 7 REST client. Use as an async context manager."""

    def __init__(self, cfg: EthzResearchCollectionConfig, *, timeout_seconds: int = 180):
        self._cfg = cfg
        self._timeout = timeout_seconds
        self._semaphore = asyncio.Semaphore(cfg.max_concurrency)
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "DSpaceClient":
        # Default headers are anonymous: discover/item/bundle/bitstream-listing
        # endpoints are public and a misconfigured/inactive `ETHZ_RESEARCH_COLLECTION_TOKEN`
        # (e.g. one whose user hasn't accepted the platform agreement) gets
        # 403'd. The bearer is only attached on demand for bitstream content
        # downloads (some PDFs/text bundles are gated).
        headers: Dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (compatible; "
                "src.index.ethz_research_collection/1.0; +https://www.research-collection.ethz.ch)"
            ),
        }
        self._client = httpx.AsyncClient(
            base_url=self._cfg.base_url.rstrip("/"),
            headers=headers,
            timeout=self._timeout,
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            msg = "DSpaceClient must be used inside `async with`."
            raise RuntimeError(msg)
        return self._client

    _RETRY_STATUSES = {405, 429, 500, 502, 503, 504}
    _RETRY_TRANSPORT_EXC = (httpx.ReadTimeout, httpx.ConnectTimeout,
                            httpx.PoolTimeout, httpx.RemoteProtocolError,
                            httpx.ConnectError)

    async def _get_json(self, path: str, **kwargs: Any) -> Dict[str, Any]:
        last_exc: Optional[Exception] = None
        for attempt in range(6):
            try:
                async with self._semaphore:
                    response = await self.client.get(path, **kwargs)
            except self._RETRY_TRANSPORT_EXC as exc:
                last_exc = exc
                delay = min(2 ** attempt, 16) + 0.25 * attempt
                logger.debug(
                    "DSpace %s on %s, retry in %.1fs (attempt %d)",
                    type(exc).__name__, path, delay, attempt + 1,
                )
                await asyncio.sleep(delay)
                continue
            if response.status_code in self._RETRY_STATUSES:
                # the ETH Research Collection's edge layer 405s under burst; back off and retry.
                last_exc = httpx.HTTPStatusError(
                    message=f"transient {response.status_code}",
                    request=response.request,
                    response=response,
                )
                delay = min(2 ** attempt, 16) + 0.25 * attempt
                logger.debug(
                    "DSpace %s on %s, retry in %.1fs (attempt %d)",
                    response.status_code, path, delay, attempt + 1,
                )
                await asyncio.sleep(delay)
                continue
            response.raise_for_status()
            return response.json()
        assert last_exc is not None
        raise last_exc

    async def discover_fulltext(
        self,
        term: str,
        *,
        configuration: str = "researchoutputs",
        page: int = 0,
        size: int = 100,
    ) -> Dict[str, Any]:
        """One page of `/discover/search/objects?query=fulltext:<term>`."""
        params = {
            "query": f"fulltext:{term}",
            "configuration": configuration,
            "page": page,
            "size": size,
        }
        return await self._get_json("/discover/search/objects", params=params)

    async def iter_discover_fulltext(
        self,
        term: str,
        *,
        configuration: str = "researchoutputs",
        size: int = 100,
        start_page: int = 0,
        max_pages: Optional[int] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Yield raw indexable item dicts across all pages for a fulltext term."""
        page = start_page
        while True:
            payload = await self.discover_fulltext(
                term, configuration=configuration, page=page, size=size,
            )
            search = payload.get("_embedded", {}).get("searchResult", {})
            page_info = search.get("page", {}) or {}
            total_pages = page_info.get("totalPages", 0) or 0
            objects = search.get("_embedded", {}).get("objects", []) or []
            for obj in objects:
                indexable = obj.get("_embedded", {}).get("indexableObject")
                if indexable is not None:
                    yield indexable
            page += 1
            if page >= total_pages:
                return
            if max_pages is not None and (page - start_page) >= max_pages:
                return

    async def get_item(self, uuid: str) -> Optional[Dict[str, Any]]:
        """`GET /core/items/{uuid}`. Returns None on 404."""
        try:
            return await self._get_json(f"/core/items/{uuid}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    async def get_bundles(self, item_uuid: str) -> List[Dict[str, Any]]:
        """List bundles for an item; returns the `_embedded.bundles` array."""
        payload = await self._get_json(f"/core/items/{item_uuid}/bundles")
        return payload.get("_embedded", {}).get("bundles", []) or []

    async def get_bitstreams(self, bundle_uuid: str) -> List[Dict[str, Any]]:
        """List bitstreams in a bundle."""
        payload = await self._get_json(f"/core/bundles/{bundle_uuid}/bitstreams")
        return payload.get("_embedded", {}).get("bitstreams", []) or []

    async def get_bitstream_content(self, bitstream_uuid: str) -> bytes:
        """Raw bytes of `/core/bitstreams/{uuid}/content`.

        Tries the bearer token first (if configured), falls back to anonymous
        on 401/403 — covers the case where the token is inactive but the
        bitstream is publicly accessible.
        """
        path = f"/core/bitstreams/{bitstream_uuid}/content"
        if self._cfg.token:
            try:
                async with self._semaphore:
                    response = await self.client.get(
                        path,
                        headers={"Authorization": f"Bearer {self._cfg.token}"},
                    )
                response.raise_for_status()
                return response.content
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in (401, 403):
                    raise
                logger.debug("Bearer rejected on %s, retrying anonymously", path)
        async with self._semaphore:
            response = await self.client.get(path)
        response.raise_for_status()
        return response.content


@asynccontextmanager
async def dspace_client(cfg: EthzResearchCollectionConfig) -> AsyncIterator[DSpaceClient]:
    """Convenience helper: `async with dspace_client(cfg) as client: ...`."""
    async with DSpaceClient(cfg) as c:
        yield c
