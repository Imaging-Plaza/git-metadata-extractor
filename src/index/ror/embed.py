"""RCP embedding client (OpenAI-compatible `/embeddings`).

Batched, retried with exponential backoff on 429/5xx, semaphore-bounded
concurrency. Asserts the server's reported embedding dim matches
`config.rcp.embedding_dim` on the first response and fails loudly on mismatch.
Designed for `Qwen/Qwen3-Embedding-8B` but works with any OpenAI-compatible
embedding model.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
from typing import List, Optional, Sequence

import httpx
import numpy as np

from .config import RcpConfig

logger = logging.getLogger(__name__)


class EmbeddingError(RuntimeError):
    """Raised on unrecoverable embedding failure."""


_MAX_RETRIES = 5
_RETRY_BASE_DELAY = 1.5
_RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


def _auth_headers(rcp: RcpConfig) -> dict:
    headers = {"Content-Type": "application/json"}
    if rcp.token:
        headers["Authorization"] = f"Bearer {rcp.token}"
    return headers


async def _post_with_retry(
    client: httpx.AsyncClient,
    url: str,
    payload: dict,
    headers: dict,
) -> dict:
    last_error: Optional[Exception] = None
    for attempt in range(_MAX_RETRIES):
        try:
            resp = await client.post(url, json=payload, headers=headers)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = exc
            delay = _RETRY_BASE_DELAY * math.pow(2, attempt) + random.random()
            logger.warning(
                "Embedding request transport error (attempt %d/%d): %s; sleeping %.1fs",
                attempt + 1, _MAX_RETRIES, exc, delay,
            )
            await asyncio.sleep(delay)
            continue
        if resp.status_code in _RETRY_STATUS:
            delay = _RETRY_BASE_DELAY * math.pow(2, attempt) + random.random()
            logger.warning(
                "Embedding request HTTP %d (attempt %d/%d); sleeping %.1fs",
                resp.status_code, attempt + 1, _MAX_RETRIES, delay,
            )
            await asyncio.sleep(delay)
            continue
        if resp.status_code != 200:
            msg = (
                f"Embedding request failed: HTTP {resp.status_code} "
                f"body={resp.text[:500]}"
            )
            raise EmbeddingError(msg)
        return resp.json()
    msg = f"Embedding request gave up after {_MAX_RETRIES} retries; last error: {last_error}"
    raise EmbeddingError(msg)


def _normalize(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    return vectors / norms


async def _embed_one_batch(
    client: httpx.AsyncClient,
    rcp: RcpConfig,
    inputs: Sequence[str],
) -> np.ndarray:
    payload = {
        "model": rcp.embedding_model,
        "input": list(inputs),
    }
    url = rcp.base_url.rstrip("/") + "/embeddings"
    body = await _post_with_retry(client, url, payload, _auth_headers(rcp))
    data = body.get("data") or []
    if len(data) != len(inputs):
        msg = (
            f"Embedding response mismatch: expected {len(inputs)} vectors, "
            f"got {len(data)}"
        )
        raise EmbeddingError(msg)
    rows = sorted(data, key=lambda d: d.get("index", 0))
    matrix = np.array([row["embedding"] for row in rows], dtype=np.float32)
    if matrix.shape[1] != rcp.embedding_dim:
        msg = (
            f"Embedding dim mismatch: server returned {matrix.shape[1]}, "
            f"config expected {rcp.embedding_dim}"
        )
        raise EmbeddingError(msg)
    return matrix


async def embed_passages(
    rcp: RcpConfig,
    texts: Sequence[str],
    *,
    normalize: bool = True,
) -> np.ndarray:
    """Embed `texts` with the configured embedding model. Returns (N, dim) float32."""
    if not texts:
        return np.zeros((0, rcp.embedding_dim), dtype=np.float32)

    semaphore = asyncio.Semaphore(rcp.max_concurrency)
    timeout = httpx.Timeout(rcp.timeout_seconds)

    async with httpx.AsyncClient(timeout=timeout) as client:
        async def _run(batch_inputs: Sequence[str]) -> np.ndarray:
            async with semaphore:
                return await _embed_one_batch(client, rcp, batch_inputs)

        batches = [
            list(texts[i : i + rcp.batch_size])
            for i in range(0, len(texts), rcp.batch_size)
        ]
        results = await asyncio.gather(*[_run(b) for b in batches])

    matrix = np.vstack(results) if results else np.zeros(
        (0, rcp.embedding_dim), dtype=np.float32,
    )
    return _normalize(matrix) if normalize else matrix


async def embed_query(
    rcp: RcpConfig,
    text: str,
    *,
    normalize: bool = True,
) -> np.ndarray:
    """Embed a single query, prefixed with `rcp.query_instruction` per Qwen3 format."""
    qtext = f"Instruct: {rcp.query_instruction}\nQuery: {text}"
    matrix = await embed_passages(rcp, [qtext], normalize=normalize)
    return matrix[0]


def embed_passages_sync(rcp: RcpConfig, texts: Sequence[str], *, normalize: bool = True) -> np.ndarray:
    return asyncio.run(embed_passages(rcp, texts, normalize=normalize))


def embed_query_sync(rcp: RcpConfig, text: str, *, normalize: bool = True) -> np.ndarray:
    return asyncio.run(embed_query(rcp, text, normalize=normalize))


__all__: List[str] = [
    "EmbeddingError",
    "embed_passages",
    "embed_passages_sync",
    "embed_query",
    "embed_query_sync",
]
