"""Qdrant client wrapper: collection bootstrap, upsert, search.

Per-entity collections: hf_models, hf_datasets, hf_spaces, hf_orgs.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from qdrant_client import QdrantClient, models

if TYPE_CHECKING:
    from src.index.huggingface.config import HuggingFaceIndexConfig

LOGGER = logging.getLogger(__name__)

# Qdrant collection names per HuggingFace entity table.
COLLECTION_FOR_TABLE: dict[str, str] = {
    "models": "hf_models",
    "datasets": "hf_datasets",
    "spaces": "hf_spaces",
    "orgs": "hf_orgs",
}


class QdrantStore:
    """Per-entity collection bootstrap + upsert + filtered search."""

    def __init__(self, config: HuggingFaceIndexConfig) -> None:
        self._config = config
        self._client = QdrantClient(
            url=config.qdrant.url,
            api_key=config.qdrant.api_key,
            prefer_grpc=config.qdrant.prefer_grpc,
        )
        self._dim = config.rcp.embedding_dim

    @property
    def client(self) -> QdrantClient:
        return self._client

    def ensure_collection(self, name: str) -> None:
        if self._client.collection_exists(name):
            return
        self._client.create_collection(
            collection_name=name,
            vectors_config=models.VectorParams(
                size=self._dim,
                distance=models.Distance.COSINE,
            ),
        )
        LOGGER.info("created qdrant collection %s (dim=%d)", name, self._dim)

    def upsert_points(
        self,
        collection: str,
        *,
        ids: list[str],
        vectors: list[list[float]],
        payloads: list[dict[str, Any]],
    ) -> None:
        if not (len(ids) == len(vectors) == len(payloads)):
            message = "ids/vectors/payloads must be the same length"
            raise ValueError(message)
        if not ids:
            return
        self.ensure_collection(collection)
        points = [
            models.PointStruct(id=pid, vector=vec, payload=payload)
            for pid, vec, payload in zip(ids, vectors, payloads, strict=False)
        ]
        self._client.upsert(collection_name=collection, points=points)

    def search(
        self,
        collection: str,
        *,
        query_vector: list[float],
        top_k: int = 50,
        filter_payload: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        self.ensure_collection(collection)
        qdrant_filter = self._build_filter(filter_payload) if filter_payload else None
        hits = self._client.query_points(
            collection_name=collection,
            query=query_vector,
            limit=top_k,
            query_filter=qdrant_filter,
            with_payload=True,
        ).points
        return [
            {"id": str(p.id), "score": float(p.score), "payload": p.payload or {}}
            for p in hits
        ]

    def count(self, collection: str) -> int:
        if not self._client.collection_exists(collection):
            return 0
        return int(self._client.count(collection_name=collection, exact=True).count)

    @staticmethod
    def _build_filter(payload: dict[str, Any]) -> models.Filter:
        must: list[models.Condition] = []
        for key, value in payload.items():
            if isinstance(value, dict) and ("gte" in value or "lte" in value):
                must.append(
                    models.FieldCondition(
                        key=key,
                        range=models.Range(
                            gte=value.get("gte"),
                            lte=value.get("lte"),
                        ),
                    ),
                )
            elif isinstance(value, list):
                must.append(
                    models.FieldCondition(key=key, match=models.MatchAny(any=value)),
                )
            else:
                must.append(
                    models.FieldCondition(key=key, match=models.MatchValue(value=value)),
                )
        return models.Filter(must=must)
