"""RCP reranker client.

Posts to `{rcp.base_url}/rerank` with `{model, query, documents}`.
Returns reranked indices + scores. Trusts the server to apply
Qwen3-Reranker-8B's internal prompt format.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import List, Optional

import httpx

from .config import RcpConfig

logger = logging.getLogger(__name__)


class RerankError(Exception):
    pass


@dataclass
class RerankHit:
    index: int
    score: float


class RCPReranker:
    def __init__(self, cfg: RcpConfig):
        self._cfg = cfg
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "RCPReranker":
        if not self._cfg.token:
            msg = "RCP_TOKEN is required to call the RCP reranker endpoint."
            raise RerankError(msg)
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
            msg = "RCPReranker must be used inside `async with`."
            raise RuntimeError(msg)
        return self._client

    async def rerank(
        self,
        query: str,
        documents: List[str],
        *,
        top_n: Optional[int] = None,
    ) -> List[RerankHit]:
        if not documents:
            return []
        body = {
            "model": self._cfg.reranker_model,
            "query": query,
            "documents": documents,
        }
        if top_n is not None:
            body["top_n"] = top_n

        last_exc: Optional[Exception] = None
        for attempt in range(4):
            try:
                response = await self.client.post("/rerank", json=body)
                response.raise_for_status()
                payload = response.json()
                break
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (429, 500, 502, 503, 504):
                    last_exc = exc
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise
            except httpx.HTTPError as exc:
                last_exc = exc
                await asyncio.sleep(2 ** attempt)
        else:
            msg = f"RCP rerank failed after retries: {last_exc}"
            raise RerankError(msg) from last_exc

        results = payload.get("results") or payload.get("data") or []
        hits: List[RerankHit] = []
        for r in results:
            idx = r.get("index")
            score = r.get("relevance_score") or r.get("score")
            if idx is None or score is None:
                continue
            hits.append(RerankHit(index=int(idx), score=float(score)))
        return hits
