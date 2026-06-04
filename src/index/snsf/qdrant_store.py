"""Qdrant client wrapper for the SNSF P3 index.

One collection per scope mode (`snsf_epfl`, `snsf_ethz`, `snsf_eth_domain`,
`snsf_switzerland`).

v3.0.0: the grant id is the canonical grant URL
(`https://data.snf.ch/grants/grant/<n>`). Qdrant point ids must be uint64 or
UUID, so points are keyed by a deterministic `uuid5` of the grant URL (the
infoscience / ethz scheme); the URL itself rides in the payload as
`grant_number` and is what search returns.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from qdrant_client import QdrantClient, models

from src.v2.canonicalization.snsf import snsf_grant_point_id

if TYPE_CHECKING:
    from src.index.snsf.config import SnsfIndexConfig

logger = logging.getLogger(__name__)


class QdrantSnsfStore:
    """Per-scope collection bootstrap, upsert, search."""

    def __init__(self, config: "SnsfIndexConfig") -> None:
        self._config = config
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
                size=self._dim, distance=models.Distance.COSINE,
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
        grant_numbers: List[str],
        vectors: List[List[float]],
        payloads: List[Dict[str, Any]],
        batch_size: int = 256,
    ) -> None:
        if not (len(grant_numbers) == len(vectors) == len(payloads)):
            msg = "grant_numbers/vectors/payloads must be the same length"
            raise ValueError(msg)
        if not grant_numbers:
            return
        name = self.ensure_collection(scope_mode)
        for start in range(0, len(grant_numbers), batch_size):
            end = start + batch_size
            points = [
                models.PointStruct(id=snsf_grant_point_id(gn), vector=vec, payload=p)
                for gn, vec, p in zip(
                    grant_numbers[start:end], vectors[start:end], payloads[start:end],
                )
            ]
            self._client.upsert(collection_name=name, points=points, wait=True)

    def search(
        self,
        scope_mode: str,
        *,
        query_vector: List[float],
        top_k: int = 50,
        institution: Optional[str] = None,
        discipline_l1: Optional[str] = None,
        state: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        name = self.collection_name(scope_mode)
        if not self._client.collection_exists(name):
            msg = (
                f"Qdrant collection {name!r} does not exist. "
                f"Run `python -m src.index.snsf embed --scope {scope_mode}` first."
            )
            raise FileNotFoundError(msg)

        must = []
        if institution:
            must.append(models.FieldCondition(
                key="research_institution", match=models.MatchValue(value=institution),
            ))
        if discipline_l1:
            must.append(models.FieldCondition(
                key="main_discipline_l1", match=models.MatchValue(value=discipline_l1),
            ))
        if state:
            must.append(models.FieldCondition(
                key="state", match=models.MatchValue(value=state),
            ))
        qfilter = models.Filter(must=must) if must else None

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
                # The point id is a uuid5(url); the canonical grant URL id
                # rides in the payload.
                "grant_number": (p.payload or {}).get("grant_number"),
                "title": (p.payload or {}).get("title", ""),
                "research_institution": (p.payload or {}).get("research_institution"),
                "main_discipline": (p.payload or {}).get("main_discipline"),
                "start_date": (p.payload or {}).get("start_date"),
                "amount_granted": (p.payload or {}).get("amount_granted"),
                "text": (p.payload or {}).get("text", ""),
            }
            for p in hits
        ]

    def count(self, scope_mode: str) -> int:
        name = self.collection_name(scope_mode)
        if not self._client.collection_exists(name):
            return 0
        return int(self._client.count(collection_name=name, exact=True).count)


__all__ = ["QdrantSnsfStore"]
