"""RCP embedding client (OpenAI-compatible /v1/embeddings).

Qwen3-Embedding-8B is instruction-aware: queries are wrapped with the
configured instruction template; passages are sent verbatim. The first
response's vector dimension is asserted against `cfg.rcp.embedding_dim`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Iterable, List, Optional

import httpx

from .config import RcpConfig

logger = logging.getLogger(__name__)


class EmbedError(Exception):
    pass


class RCPEmbedder:
    """Async embedding client. Use as an async context manager."""

    def __init__(self, cfg: RcpConfig):
        self._cfg = cfg
        self._client: Optional[httpx.AsyncClient] = None
        self._semaphore = asyncio.Semaphore(cfg.max_concurrency)
        self._observed_dim: Optional[int] = None

    async def __aenter__(self) -> "RCPEmbedder":
        if not self._cfg.token:
            msg = "RCP_TOKEN is required to call the RCP embedding endpoint."
            raise EmbedError(msg)
        self._client = httpx.AsyncClient(
            base_url=self._cfg.base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {self._cfg.token}",
                "Content-Type": "application/json",
            },
            timeout=self._cfg.timeout_seconds,
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            msg = "RCPEmbedder must be used inside `async with`."
            raise RuntimeError(msg)
        return self._client

    async def _post_embeddings(self, inputs: List[str]) -> List[List[float]]:
        last_exc: Optional[Exception] = None
        for attempt in range(4):
            try:
                async with self._semaphore:
                    response = await self.client.post(
                        "/embeddings",
                        json={"model": self._cfg.embedding_model, "input": inputs},
                    )
                response.raise_for_status()
                payload = response.json()
                break
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (429, 500, 502, 503, 504):
                    last_exc = exc
                    delay = 2 ** attempt
                    logger.warning(
                        "RCP embeddings %s, retry in %ds (attempt %d)",
                        exc.response.status_code, delay, attempt + 1,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise
            except httpx.HTTPError as exc:
                last_exc = exc
                delay = 2 ** attempt
                logger.warning("RCP embeddings transport error: %s, retry in %ds", exc, delay)
                await asyncio.sleep(delay)
        else:
            msg = f"RCP embeddings failed after retries: {last_exc}"
            raise EmbedError(msg) from last_exc

        vectors = [item["embedding"] for item in payload.get("data", [])]
        if not vectors:
            msg = "RCP embeddings returned empty response."
            raise EmbedError(msg)

        observed = len(vectors[0])
        if self._observed_dim is None:
            self._observed_dim = observed
            if observed != self._cfg.embedding_dim:
                msg = (
                    f"Embedding dim mismatch: configured "
                    f"{self._cfg.embedding_dim}, RCP returned {observed}."
                )
                raise EmbedError(msg)
        return vectors

    async def embed_passage(self, texts: Iterable[str]) -> List[List[float]]:
        """Embed plain passage strings (no instruction prefix)."""
        batch = list(texts)
        if not batch:
            return []
        return await self._post_embeddings(batch)

    async def embed_query(
        self,
        query: str,
        instruction: Optional[str] = None,
    ) -> List[float]:
        """Embed a query with the Qwen3 instruction template."""
        instr = instruction or self._cfg.query_instruction
        formatted = f"Instruct: {instr}\nQuery: {query}"
        vectors = await self._post_embeddings([formatted])
        return vectors[0]

    async def embed_passages_batched(
        self,
        texts: List[str],
    ) -> List[List[float]]:
        """Embed `texts` in batches of `cfg.batch_size`, preserving order."""
        out: List[List[float]] = []
        for i in range(0, len(texts), self._cfg.batch_size):
            chunk = texts[i : i + self._cfg.batch_size]
            out.extend(await self.embed_passage(chunk))
        return out
