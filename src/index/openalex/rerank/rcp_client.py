"""Async client for the RCP reranker (Cohere-compatible shape).

If the RCP deployment exposes a different path or body shape, override
`RCP_RERANK_PATH` via env or pass `path=` at construction time.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

if TYPE_CHECKING:
    from src.index.openalex.config import OpenAlexIndexConfig

LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 30.0
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS
    return False


class RCPRerankerClient:
    def __init__(
        self,
        config: OpenAlexIndexConfig,
        *,
        path: str | None = None,
        timeout_s: float | None = None,
    ) -> None:
        config.require_rcp()
        self._config = config
        self._timeout = httpx.Timeout(timeout_s or DEFAULT_TIMEOUT_S)
        path = path or os.getenv("RCP_RERANK_PATH", "/rerank")
        self._url = f"{config.rcp.base_url.rstrip('/')}{path}"
        self._headers = {
            "Authorization": f"Bearer {config.rcp.token}",
            "Content-Type": "application/json",
        }

    @property
    def model(self) -> str:
        return self._config.rcp.reranker_model

    async def rerank(
        self,
        query: str,
        documents: list[str],
        *,
        top_n: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return [{index, relevance_score}, ...] sorted by relevance desc."""
        if not documents:
            return []
        payload: dict[str, Any] = {
            "model": self._config.rcp.reranker_model,
            "query": query,
            "documents": documents,
        }
        if top_n is not None:
            payload["top_n"] = top_n

        @retry(
            stop=stop_after_attempt(5),
            wait=wait_exponential(multiplier=1, min=2, max=30),
            retry=retry_if_exception_type((httpx.TimeoutException, httpx.HTTPStatusError)),
            reraise=True,
        )
        async def _call() -> list[dict[str, Any]]:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self._url,
                    headers=self._headers,
                    json=payload,
                    timeout=self._timeout,
                )
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    if not _is_retryable(exc):
                        body = response.text[:500]
                        LOGGER.error(
                            "RCP rerank non-retryable %d: %s",
                            response.status_code,
                            body,
                        )
                    raise
                data = response.json()
            results = data.get("results") or []
            return [
                {
                    "index": int(item["index"]),
                    "relevance_score": float(item["relevance_score"]),
                }
                for item in results
            ]

        return await _call()
