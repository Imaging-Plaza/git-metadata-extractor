"""Qdrant client wrapper for the ROR index.

One collection per scope mode (`ror_epfl_ethz`, `ror_switzerland`,
`ror_europe`, `ror_worldwide`). Cosine distance, dim from RCP config.
Mirrors the shape of `src/index/openalex/vector/qdrant_store.py`.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from qdrant_client import QdrantClient, models

if TYPE_CHECKING:
    from .config import RorIndexConfig

logger = logging.getLogger(__name__)


def _stable_point_id(ror_id: str) -> str:
    """Map a ROR URL like https://ror.org/02s376052 to a stable UUIDv5 string.

    Qdrant point IDs accept either int64 or UUID; UUIDv5 over a fixed namespace
    keeps the mapping deterministic across rebuilds.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, ror_id))


class QdrantRorStore:
    """Per-scope collection bootstrap, upsert, search."""

    def __init__(self, config: "RorIndexConfig") -> None:
        self._config = config
        # qdrant-client's default gRPC timeout (~5s) is too tight for large
        # `wait=True` flushes (e.g. the final batch of a 125k-record upsert).
        # Use the same timeout as the RCP HTTP timeout — it's already tuned
        # for "this might take a while" calls.
        self._client = QdrantClient(
            url=config.qdrant.url,
            api_key=config.qdrant.api_key,
            prefer_grpc=config.qdrant.prefer_grpc,
            timeout=max(config.rcp.timeout_seconds, 120),
        )
        self._dim = config.rcp.embedding_dim

    @property
    def client(self) -> QdrantClient:
        return self._client

    def collection_name(self, scope_mode: Optional[str] = None) -> str:
        if scope_mode is None:
            return self._config.collection_name()
        return f"{self._config.qdrant.collection_prefix}_{scope_mode}"

    def ensure_collection(self, scope_mode: Optional[str] = None) -> str:
        name = self.collection_name(scope_mode)
        if self._client.collection_exists(name):
            return name
        self._client.create_collection(
            collection_name=name,
            vectors_config=models.VectorParams(
                size=self._dim,
                distance=models.Distance.COSINE,
            ),
        )
        logger.info("created qdrant collection %s (dim=%d)", name, self._dim)
        return name

    def recreate_collection(self, scope_mode: Optional[str] = None) -> str:
        name = self.collection_name(scope_mode)
        if self._client.collection_exists(name):
            self._client.delete_collection(name)
            logger.info("dropped existing qdrant collection %s", name)
        return self.ensure_collection(scope_mode)

    def upsert_records(
        self,
        scope_mode: str,
        *,
        ror_ids: List[str],
        vectors: List[List[float]],
        payloads: List[Dict[str, Any]],
        batch_size: int = 256,
    ) -> None:
        if not (len(ror_ids) == len(vectors) == len(payloads)):
            msg = "ror_ids/vectors/payloads must be the same length"
            raise ValueError(msg)
        if not ror_ids:
            return
        name = self.ensure_collection(scope_mode)
        total = len(ror_ids)
        for start in range(0, total, batch_size):
            end = start + batch_size
            points = [
                models.PointStruct(
                    id=_stable_point_id(rid),
                    vector=vec,
                    payload=payload,
                )
                for rid, vec, payload in zip(
                    ror_ids[start:end], vectors[start:end], payloads[start:end],
                )
            ]
            # wait=True per batch keeps each flush small and within timeout
            # (vs. one giant flush at the end that can take many minutes for
            # the 125k-record worldwide scope).
            self._client.upsert(collection_name=name, points=points, wait=True)

    def search(
        self,
        scope_mode: str,
        *,
        query_vector: List[float],
        top_k: int = 50,
        country: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        name = self.collection_name(scope_mode)
        if not self._client.collection_exists(name):
            msg = (
                f"Qdrant collection {name!r} does not exist. "
                f"Run `python -m src.index.ror build` first."
            )
            raise FileNotFoundError(msg)
        qfilter = None
        if country:
            qfilter = models.Filter(must=[
                models.FieldCondition(
                    key="country_code", match=models.MatchValue(value=country.upper()),
                ),
            ])
        hits = self._client.query_points(
            collection_name=name,
            query=query_vector,
            limit=top_k,
            query_filter=qfilter,
            with_payload=True,
        ).points
        return [
            {
                "score": float(p.score),
                "ror_id": str((p.payload or {}).get("ror_id", "")),
                "name": (p.payload or {}).get("name"),
                "text": (p.payload or {}).get("text", ""),
                "record": (p.payload or {}).get("record", {}),
            }
            for p in hits
        ]

    def count(self, scope_mode: str) -> int:
        name = self.collection_name(scope_mode)
        if not self._client.collection_exists(name):
            return 0
        return int(self._client.count(collection_name=name, exact=True).count)


__all__ = ["QdrantRorStore", "_stable_point_id"]
