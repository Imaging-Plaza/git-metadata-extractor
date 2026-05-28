"""Qdrant client wrapper for the ETH Research Collection index.

Per-entity collections following the canonical pattern in this repo:

    * ``ethz_research_collection_chunks``        — publication body fragments
    * ``ethz_research_collection_articles``      — one row per matched publication
    * ``ethz_research_collection_persons``       — Person entities resolved from author
                                       authority IDs
    * ``ethz_research_collection_organizations`` — OrgUnit entities

See `.internal/ror/qdrant-setup.md` for the broader convention.

Qdrant is a server-side store; configuration comes from
`config.qdrant` (URL, gRPC toggle, optional API key) which respects the
`INDEX_QDRANT_*` env-var overrides at config-load time.

Point IDs are deterministic UUIDv5 over a stable string key:

    * chunks: ``"{article_uuid}::{chunk_index}"``
    * articles: ``article_uuid``
    * persons: ``person_uuid``
    * organizations: ``org_uuid``

Payloads carry the full entity record so structured filters work natively
(no list-flattening tricks needed).
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Iterable, List, Optional, Sequence

from qdrant_client import QdrantClient, models

from .config import EthzResearchCollectionIndexConfig, QdrantConfig
from .models import (
    ArticleRecord,
    ChunkRecord,
    OrganizationRecord,
    PersonRecord,
)

logger = logging.getLogger(__name__)

CHUNKS_COLLECTION = "ethz_research_collection_chunks"
ARTICLES_COLLECTION = "ethz_research_collection_articles"
PERSONS_COLLECTION = "ethz_research_collection_persons"
ORGANIZATIONS_COLLECTION = "ethz_research_collection_organizations"

ALL_COLLECTIONS: tuple[str, ...] = (
    CHUNKS_COLLECTION,
    ARTICLES_COLLECTION,
    PERSONS_COLLECTION,
    ORGANIZATIONS_COLLECTION,
)

# Stable namespace for UUIDv5 point IDs scoped to this index. Distinct
# from the infoscience namespace so points can't collide if both indices
# ever share a Qdrant DB.
_INDEX_NAMESPACE = uuid.UUID("8d49bc05-751a-457f-bd25-6630ea5fdac4")

_GH_HOSTS = ("github.com",)
_HF_HOSTS = ("huggingface.co", "hf.co")


def _has_host(urls: Sequence[str], hosts: Iterable[str]) -> bool:
    if not urls:
        return False
    needles = tuple(hosts)
    return any(any(h in u for h in needles) for u in urls)


def _point_id(key: str) -> str:
    return str(uuid.uuid5(_INDEX_NAMESPACE, key))


def _drop_none(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in payload.items() if v is not None}


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------


def chunk_payload(rec: ChunkRecord) -> dict[str, Any]:
    return _drop_none({
        "chunk_id": rec.chunk_id,
        "article_uuid": rec.article_uuid,
        "chunk_index": rec.chunk_index,
        "text": rec.text,
        "title": rec.title,
        "abstract": rec.abstract,
        "authors": rec.authors or None,
        "author_uuids": rec.author_uuids or None,
        "doi": rec.doi,
        "publication_date": rec.publication_date,
        "year": rec.year,
        "publication_type": rec.publication_type,
        "language": rec.language,
        "subjects": rec.subjects or None,
        "keywords": rec.keywords or None,
        "lab": rec.lab,
        "lab_uuid": rec.lab_uuid,
        "org_uuids": rec.org_uuids or None,
        "research_collection_url": rec.research_collection_url,
        "matched_urls": rec.matched_urls or None,
        "has_github_match": _has_host(rec.matched_urls, _GH_HOSTS),
        "has_hf_match": _has_host(rec.matched_urls, _HF_HOSTS),
    })


def article_payload(rec: ArticleRecord) -> dict[str, Any]:
    return _drop_none({
        "article_uuid": rec.article_uuid,
        "title": rec.title,
        "abstract": rec.abstract,
        "keywords": rec.keywords or None,
        "subjects": rec.subjects or None,
        "authors": rec.authors or None,
        "author_uuids": rec.author_uuids or None,
        "doi": rec.doi,
        "publication_date": rec.publication_date,
        "year": rec.year,
        "publication_type": rec.publication_type,
        "language": rec.language,
        "journal": rec.journal,
        "journal_uuid": rec.journal_uuid,
        "scopus_id": rec.scopus_id,
        "wos_id": rec.wos_id,
        "journal_volume": rec.journal_volume,
        "journal_issue": rec.journal_issue,
        "pages_start": rec.pages_start,
        "journal_abbreviated": rec.journal_abbreviated,
        "publisher": rec.publisher,
        "issn": rec.issn,
        "handle_uri": rec.handle_uri,
        "lab": rec.lab,
        "lab_uuid": rec.lab_uuid,
        "org_uuids": rec.org_uuids or None,
        "research_collection_url": rec.research_collection_url,
        "matched_urls": rec.matched_urls or None,
        "chunk_count": rec.chunk_count,
        "has_github_match": _has_host(rec.matched_urls, _GH_HOSTS),
        "has_hf_match": _has_host(rec.matched_urls, _HF_HOSTS),
    })


def person_payload(rec: PersonRecord) -> dict[str, Any]:
    return _drop_none({
        "person_uuid": rec.person_uuid,
        "name": rec.name,
        "given_name": rec.given_name,
        "family_name": rec.family_name,
        "orcid": rec.orcid,
        "sciper_id": rec.sciper_id,
        "scopus_id": rec.scopus_id,
        "email_hash": rec.email_hash,
        "primary_affiliation": rec.primary_affiliation,
        "primary_affiliation_uuid": rec.primary_affiliation_uuid,
        "affiliation_uuids": rec.affiliation_uuids or None,
        "position": rec.position,
        "biography": rec.biography,
        "research_interests": rec.research_interests or None,
        "profile_url": rec.profile_url,
        "related_article_uuids": rec.related_article_uuids or None,
    })


def organization_payload(rec: OrganizationRecord) -> dict[str, Any]:
    return _drop_none({
        "org_uuid": rec.org_uuid,
        "name": rec.name,
        "acronym": rec.acronym,
        "aliases": rec.aliases or None,
        "parent_org_uuid": rec.parent_org_uuid,
        "parent_org_chain": rec.parent_org_chain or None,
        "parent_org_chain_names": rec.parent_org_chain_names or None,
        "description": rec.description,
        "sciper_unit_id": rec.sciper_unit_id,
        "ror_id": rec.ror_id,
        "unit_manager_uuid": rec.unit_manager_uuid,
        "unit_manager_name": rec.unit_manager_name,
        "research_collection_url": rec.research_collection_url,
        "related_article_uuids": rec.related_article_uuids or None,
    })


# ---------------------------------------------------------------------------
# Store wrapper
# ---------------------------------------------------------------------------

# Qdrant default body limit is 32 MB; 64 × ~40 KB vector + payload ≈ 3 MB.
_UPSERT_BATCH_SIZE = 64


class QdrantStore:
    """Per-entity collection bootstrap + upsert + filtered search."""

    def __init__(self, qcfg: QdrantConfig, *, vector_size: int):
        self._qcfg = qcfg
        self._dim = vector_size
        # qdrant-client's default 5s timeout is too tight for 64-point
        # batches of 4096-dim vectors with payload (~3 MB body) — large
        # ingest runs hit ResponseHandlingException("timed out") under
        # load. 120s mirrors the ROR store's tuning.
        self._client = QdrantClient(
            url=qcfg.url,
            api_key=qcfg.api_key,
            prefer_grpc=qcfg.prefer_grpc,
            timeout=120,
        )

    @classmethod
    def from_config(cls, cfg: EthzResearchCollectionIndexConfig) -> "QdrantStore":
        return cls(cfg.qdrant, vector_size=cfg.rcp.embedding_dim)

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
        logger.info("created qdrant collection %s (dim=%d)", name, self._dim)

    def collection_count(self, name: str) -> int:
        if not self._client.collection_exists(name):
            return 0
        return int(self._client.count(collection_name=name, exact=True).count)

    def upsert_points(
        self,
        collection: str,
        *,
        ids: Sequence[str],
        vectors: Sequence[Sequence[float]],
        payloads: Sequence[dict[str, Any]],
    ) -> int:
        if not (len(ids) == len(vectors) == len(payloads)):
            msg = "ids/vectors/payloads must be the same length"
            raise ValueError(msg)
        if not ids:
            return 0
        self.ensure_collection(collection)
        points = [
            models.PointStruct(id=pid, vector=list(vec), payload=payload)
            for pid, vec, payload in zip(ids, vectors, payloads, strict=False)
        ]
        # Qdrant rejects request bodies above 32 MB. A 4096-dim float vector
        # serialises to ~40 KB, so a chunks payload with body text easily
        # crosses the cap in one shot — split into sub-batches.
        for start in range(0, len(points), _UPSERT_BATCH_SIZE):
            self._client.upsert(
                collection_name=collection,
                points=points[start : start + _UPSERT_BATCH_SIZE],
            )
        return len(points)

    def search(
        self,
        collection: str,
        *,
        query_vector: Sequence[float],
        top_k: int = 50,
        query_filter: Optional[models.Filter] = None,
    ) -> List[dict[str, Any]]:
        if not self._client.collection_exists(collection):
            return []
        hits = self._client.query_points(
            collection_name=collection,
            query=list(query_vector),
            limit=top_k,
            query_filter=query_filter,
            with_payload=True,
        ).points
        return [
            {"id": str(p.id), "score": float(p.score), "payload": p.payload or {}}
            for p in hits
        ]

    def lookup(
        self,
        collection: str,
        *,
        ids: Sequence[str],
    ) -> List[dict[str, Any]]:
        """Read-only retrieval by point IDs."""
        if not ids or not self._client.collection_exists(collection):
            return []
        records = self._client.retrieve(
            collection_name=collection,
            ids=list(ids),
            with_payload=True,
        )
        return [
            {"id": str(r.id), "payload": r.payload or {}}
            for r in records
        ]

    def scroll(
        self,
        collection: str,
        *,
        query_filter: Optional[models.Filter] = None,
        limit: int = 50,
    ) -> List[dict[str, Any]]:
        """Filter-only listing (no vector); returns up to `limit` points."""
        if not self._client.collection_exists(collection):
            return []
        records, _next = self._client.scroll(
            collection_name=collection,
            scroll_filter=query_filter,
            limit=limit,
            with_payload=True,
        )
        return [
            {"id": str(r.id), "payload": r.payload or {}}
            for r in records
        ]


# ---------------------------------------------------------------------------
# Filter builder
# ---------------------------------------------------------------------------


def build_filter(payload: Optional[dict[str, Any]]) -> Optional[models.Filter]:
    """Translate a JSON-style filter dict into a Qdrant ``Filter``.

    Supported operators per key:
        * ``{"$eq": value}`` (or scalar shorthand)
        * ``{"$ne": value}``
        * ``{"$gte": v, "$lte": v}`` (range)
        * ``{"$in": [v, ...]}`` (any of)
        * ``{"$contains": "needle"}`` for list-of-string fields
        * raw ``[...]`` shorthand → ``MatchAny``
    """
    if not payload:
        return None
    must: list[models.Condition] = []
    must_not: list[models.Condition] = []
    for key, value in payload.items():
        if isinstance(value, dict):
            if "$ne" in value:
                must_not.append(models.FieldCondition(
                    key=key, match=models.MatchValue(value=value["$ne"]),
                ))
                continue
            if "$in" in value:
                must.append(models.FieldCondition(
                    key=key, match=models.MatchAny(any=list(value["$in"])),
                ))
                continue
            if "$contains" in value:
                must.append(models.FieldCondition(
                    key=key, match=models.MatchValue(value=value["$contains"]),
                ))
                continue
            if "$gte" in value or "$lte" in value:
                must.append(models.FieldCondition(
                    key=key,
                    range=models.Range(
                        gte=value.get("$gte"), lte=value.get("$lte"),
                    ),
                ))
                continue
            if "$eq" in value:
                must.append(models.FieldCondition(
                    key=key, match=models.MatchValue(value=value["$eq"]),
                ))
                continue
            msg = f"Unsupported operator dict for {key!r}: {value!r}"
            raise ValueError(msg)
        if isinstance(value, list):
            must.append(models.FieldCondition(
                key=key, match=models.MatchAny(any=value),
            ))
            continue
        must.append(models.FieldCondition(
            key=key, match=models.MatchValue(value=value),
        ))
    return models.Filter(
        must=must or None,
        must_not=must_not or None,
    )


# ---------------------------------------------------------------------------
# Convenience upsert helpers for each entity type
# ---------------------------------------------------------------------------


def upsert_chunks(
    store: QdrantStore,
    records: Sequence[ChunkRecord],
    embeddings: Sequence[Sequence[float]],
) -> int:
    if not records:
        return 0
    ids = [_point_id(r.chunk_id) for r in records]
    payloads = [chunk_payload(r) for r in records]
    return store.upsert_points(
        CHUNKS_COLLECTION, ids=ids, vectors=list(embeddings), payloads=payloads,
    )


def upsert_articles(
    store: QdrantStore,
    records: Sequence[ArticleRecord],
    embeddings: Sequence[Optional[Sequence[float]]],
    dim: int,
) -> int:
    if not records:
        return 0
    placeholder = [0.0] * dim
    ids = [_point_id(r.article_uuid) for r in records]
    vectors = [list(e) if e is not None else placeholder for e in embeddings]
    payloads = [article_payload(r) for r in records]
    return store.upsert_points(
        ARTICLES_COLLECTION, ids=ids, vectors=vectors, payloads=payloads,
    )


def upsert_persons(
    store: QdrantStore,
    records: Sequence[PersonRecord],
    embeddings: Sequence[Optional[Sequence[float]]],
    dim: int,
) -> int:
    if not records:
        return 0
    placeholder = [0.0] * dim
    ids = [_point_id(r.person_uuid) for r in records]
    vectors = [list(e) if e is not None else placeholder for e in embeddings]
    payloads = [person_payload(r) for r in records]
    return store.upsert_points(
        PERSONS_COLLECTION, ids=ids, vectors=vectors, payloads=payloads,
    )


def upsert_organizations(
    store: QdrantStore,
    records: Sequence[OrganizationRecord],
    embeddings: Sequence[Optional[Sequence[float]]],
    dim: int,
) -> int:
    if not records:
        return 0
    placeholder = [0.0] * dim
    ids = [_point_id(r.org_uuid) for r in records]
    vectors = [list(e) if e is not None else placeholder for e in embeddings]
    payloads = [organization_payload(r) for r in records]
    return store.upsert_points(
        ORGANIZATIONS_COLLECTION, ids=ids, vectors=vectors, payloads=payloads,
    )


def article_point_id(article_uuid: str) -> str:
    return _point_id(article_uuid)


def person_point_id(person_uuid: str) -> str:
    return _point_id(person_uuid)


def organization_point_id(org_uuid: str) -> str:
    return _point_id(org_uuid)
