"""Async OpenAI-compatible client for the RCP ``/embeddings`` endpoint.

Shared across every index module (openalex, orcid, github_*, huggingface_*,
zenodo_*, oamonitor, renkulab, swissubase, infoscience, ethz_research_collection,
snsf, epfl_graph). The class only touches ``config.rcp.*`` and
``config.require_rcp()``; any per-index config that exposes those is
acceptable — see ``RCPConfigProtocol`` below for the exact contract.

Modules that want tighter typing (e.g. ORCID) can subclass and re-type
the constructor argument; see ``src/index/orcid/embed/rcp_client.py``
for the canonical pattern.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Protocol

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


class _RCPBlock(Protocol):
    base_url: str
    batch_size: int
    timeout_seconds: int
    token: str | None


class RCPConfigProtocol(Protocol):
    """Minimum contract any index config must satisfy to be passed to
    ``RCPEmbeddingClient``. ``OpenAlexIndexConfig``, ``OrcidIndexConfig``,
    ``HFEntityIndexConfigBase``, ``AccountIndexConfigBase``,
    ``GitHubIndexConfig``, etc. all match this shape via duck typing.
    """

    rcp: _RCPBlock

    def require_rcp(self) -> None: ...


if TYPE_CHECKING:
    # Kept under TYPE_CHECKING so the original `OpenAlexIndexConfig`
    # type-only import doesn't pull the heavyweight pydantic graph at
    # runtime. Callers can pass any RCPConfigProtocol-shaped object.
    pass

LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 60.0
DEFAULT_BATCH_SIZE = 32

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class RCPEmbeddingError(RuntimeError):
    pass


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS
    return False


class RCPEmbeddingClient:
    """Thin async wrapper around the RCP `/embeddings` endpoint."""

    def __init__(
        self,
        config: RCPConfigProtocol,
        *,
        batch_size: int | None = None,
        timeout_s: float | None = None,
    ) -> None:
        config.require_rcp()
        self._config = config
        self._batch_size = batch_size or config.rcp.batch_size
        self._timeout = httpx.Timeout(timeout_s or config.rcp.timeout_seconds)
        self._url = f"{config.rcp.base_url.rstrip('/')}/embeddings"
        self._headers = {
            "Authorization": f"Bearer {config.rcp.token}",
            "Content-Type": "application/json",
        }

    @property
    def batch_size(self) -> int:
        return self._batch_size

    @property
    def model(self) -> str:
        return self._config.rcp.embedding_model

    async def embed_batch(
        self,
        client: httpx.AsyncClient,
        inputs: list[str],
    ) -> list[list[float]]:
        @retry(
            stop=stop_after_attempt(5),
            wait=wait_exponential(multiplier=1, min=2, max=30),
            retry=retry_if_exception_type((httpx.TimeoutException, httpx.HTTPStatusError)),
            reraise=True,
        )
        async def _call() -> list[list[float]]:
            response = await client.post(
                self._url,
                headers=self._headers,
                json={
                    "model": self._config.rcp.embedding_model,
                    "input": inputs,
                    "encoding_format": "float",
                },
                timeout=self._timeout,
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if not _is_retryable(exc):
                    body = response.text[:500]
                    LOGGER.error("RCP embed non-retryable %d: %s", response.status_code, body)
                raise
            payload: dict[str, Any] = response.json()
            data = payload.get("data") or []
            if len(data) != len(inputs):
                message = (
                    f"RCP returned {len(data)} embeddings for {len(inputs)} inputs"
                )
                raise RCPEmbeddingError(message)
            return [item["embedding"] for item in data]

        return await _call()

    async def embed_all(self, inputs: list[str]) -> list[list[float]]:
        """Embed a list of inputs, batching internally."""
        if not inputs:
            return []
        out: list[list[float]] = []
        async with httpx.AsyncClient() as client:
            for start in range(0, len(inputs), self._batch_size):
                batch = inputs[start : start + self._batch_size]
                vecs = await self.embed_batch(client, batch)
                out.extend(vecs)
        return out
