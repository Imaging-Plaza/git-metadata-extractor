"""Synchronous client around the OAM-CH Mongo-proxy API.

The upstream API exposes a single ``GET /api/data/{database}`` endpoint that
accepts a MongoDB command as a URL-encoded JSON ``query`` parameter and
returns the standard cursor envelope ``{cursor:{firstBatch:[...], id:<long>,
ns:...}, ok:1}``. No auth required.

This module wraps the two operations we actually need today — single-record
``find_one`` and cursor-paginated ``iter_documents`` — leaving room for
more MongoDB verbs (``count``, ``aggregate`` …) when there's a concrete
caller. Cursor continuation via ``getMore`` is opaque from the API docs but
the in-process page-loop with ``filter`` + ``skip`` works for our scale.
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

import requests

if TYPE_CHECKING:
    from collections.abc import Iterator

    from src.index.oamonitor.config import OamonitorIndexConfig

LOGGER = logging.getLogger(__name__)

USER_AGENT = (
    "git-metadata-extractor/2.0 (+https://github.com/Imaging-Plaza/"
    "git-metadata-extractor) (oamonitor RAG indexer)"
)


class OamonitorClient:
    """Thin synchronous client for the OAM-CH Mongo-proxy API."""

    def __init__(self, config: OamonitorIndexConfig) -> None:
        self._config = config
        self._session = requests.Session()
        self._session.headers["User-Agent"] = USER_AGENT
        self._interval = 1.0 / max(1, int(config.oamonitor.rate_per_minute / 60) or 1)
        self._last_call_at = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call_at
        if elapsed < self._interval:
            time.sleep(self._interval - elapsed)
        self._last_call_at = time.monotonic()

    def _send(self, query: dict[str, Any]) -> dict[str, Any]:
        """Execute one MongoDB command and return the parsed response body."""
        self._throttle()
        response = self._session.get(
            self._config.oamonitor.base_url,
            params={"query": json.dumps(query, ensure_ascii=False, default=str)},
            timeout=self._config.oamonitor.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            message = f"OAM API returned non-object payload: {type(payload).__name__}"
            raise RuntimeError(message)
        return payload

    def find_one(
        self, collection: str, *, _id: str,
    ) -> dict[str, Any] | None:
        """Fetch a single document by ``_id``. ``None`` on miss."""
        payload = self._send(
            {"find": collection, "filter": {"_id": _id}, "limit": 1},
        )
        batch = (payload.get("cursor") or {}).get("firstBatch") or []
        if not isinstance(batch, list) or not batch:
            return None
        first = batch[0]
        return first if isinstance(first, dict) else None

    def iter_documents(
        self,
        collection: str,
        *,
        filter: dict[str, Any] | None = None,  # noqa: A002 — MongoDB term
        limit: int | None = None,
        skip: int = 0,
    ) -> Iterator[dict[str, Any]]:
        """Stream documents from ``collection`` matching ``filter``.

        Continues via ``skip`` pagination. The upstream API silently caps
        each response at ~100 documents regardless of the ``limit`` we
        send, so we only terminate when the server returns an *empty*
        batch (or when the caller's ``limit`` cap is reached).

        ``skip`` lets a caller resume from a server-side offset — useful for
        batch-mode runs (e.g. 100K-doc batches over the 850K Publications
        collection).
        """
        page_size = max(1, int(self._config.oamonitor.page_size))
        cap = int(limit) if limit is not None else None
        yielded = 0
        skip = max(0, int(skip))
        while True:
            remaining_cap: int | None = (cap - yielded) if cap is not None else None
            batch_limit = page_size
            if remaining_cap is not None:
                batch_limit = min(batch_limit, remaining_cap)
                if batch_limit <= 0:
                    return
            query: dict[str, Any] = {
                "find": collection,
                "limit": batch_limit,
                "skip": skip,
            }
            if filter:
                query["filter"] = filter
            payload = self._send(query)
            batch = (payload.get("cursor") or {}).get("firstBatch") or []
            if not isinstance(batch, list) or not batch:
                return
            advanced = 0
            for doc in batch:
                if not isinstance(doc, dict):
                    continue
                yield doc
                yielded += 1
                advanced += 1
                if cap is not None and yielded >= cap:
                    return
            # Always advance by however much we got back — even when the
            # server returned fewer documents than ``batch_limit``, that's
            # the server-side page cap, not the end of the collection.
            if advanced == 0:
                return
            skip += advanced


__all__ = ["OamonitorClient"]
