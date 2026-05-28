"""Qdrant client wrapper: collection bootstrap, upsert, search."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from qdrant_client import QdrantClient, models
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

if TYPE_CHECKING:
    from src.index.openalex.config import OpenAlexIndexConfig

LOGGER = logging.getLogger(__name__)

# Qdrant HTTP client default timeout is too short for 4096-dim batch upserts;
# bump it. Transient failures are retried with exponential backoff.
QDRANT_HTTP_TIMEOUT_S = 60
QDRANT_RETRY_ATTEMPTS = 5

PER_ENTITY_COLLECTIONS: tuple[str, ...] = (
    "works",
    "authors",
    "institutions",
    "sources",
    "topics",
    "concepts",
)


class QdrantStore:
    """Per-entity collection bootstrap + upsert + filtered search."""

    def __init__(self, config: OpenAlexIndexConfig) -> None:
        self._config = config
        self._client = QdrantClient(
            url=config.qdrant.url,
            api_key=config.qdrant.api_key,
            prefer_grpc=config.qdrant.prefer_grpc,
            timeout=QDRANT_HTTP_TIMEOUT_S,
        )
        self._dim = config.rcp.embedding_dim

    @property
    def client(self) -> QdrantClient:
        return self._client

    def ensure_collection(self, name: str) -> None:
        """Create the collection if missing. Idempotent."""
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

        @retry(
            stop=stop_after_attempt(QDRANT_RETRY_ATTEMPTS),
            wait=wait_exponential(multiplier=1, min=2, max=30),
            retry=retry_if_exception_type(
                (ResponseHandlingException, UnexpectedResponse),
            ),
            reraise=True,
        )
        def _do_upsert() -> None:
            self._client.upsert(collection_name=collection, points=points)

        _do_upsert()

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
