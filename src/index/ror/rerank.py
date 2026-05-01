"""RCP reranker client (Cohere-compatible `/rerank`).

Posts `{model, query, documents}` and reads back `{results: [{index, relevance_score}, ...]}`.
This shape is what vLLM, Infinity, and most modern inference gateways expose
for cross-encoder / Qwen3-Reranker-style models. If RCP later switches the
schema, only this module needs updating.
"""

from __future__ import annotations

import logging
from typing import List, Sequence

import httpx
from pydantic import BaseModel

from .config import RcpConfig
from .embed import _auth_headers, _post_with_retry  # noqa: PLC2701

logger = logging.getLogger(__name__)


class RerankError(RuntimeError):
    """Raised on unrecoverable rerank failure."""


class RerankResult(BaseModel):
    index: int
    score: float


async def rerank(
    rcp: RcpConfig,
    query: str,
    documents: Sequence[str],
    *,
    top_n: int | None = None,
) -> List[RerankResult]:
    """Rerank `documents` by relevance to `query`. Returns descending score order."""
    if not documents:
        return []

    payload = {
        "model": rcp.reranker_model,
        "query": query,
        "documents": list(documents),
    }
    if top_n is not None:
        payload["top_n"] = top_n

    url = rcp.base_url.rstrip("/") + "/rerank"
    timeout = httpx.Timeout(rcp.timeout_seconds)

    async with httpx.AsyncClient(timeout=timeout) as client:
        body = await _post_with_retry(client, url, payload, _auth_headers(rcp))

    raw_results = body.get("results")
    if not isinstance(raw_results, list):
        msg = f"Rerank response missing 'results' list; got keys {list(body.keys())}"
        raise RerankError(msg)

    out: List[RerankResult] = []
    for entry in raw_results:
        if not isinstance(entry, dict):
            continue
        idx = entry.get("index")
        score = entry.get("relevance_score", entry.get("score"))
        if not isinstance(idx, int) or not isinstance(score, (int, float)):
            continue
        out.append(RerankResult(index=idx, score=float(score)))

    out.sort(key=lambda r: r.score, reverse=True)
    return out


__all__: List[str] = ["RerankError", "RerankResult", "rerank"]
