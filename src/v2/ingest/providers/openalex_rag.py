"""Async RAG provider over the OpenAlex Qdrant index.

Six per-entity collections: ``works`` / ``authors`` / ``institutions`` /
``sources`` / ``topics`` / ``concepts``.

OpenAlex chunks carry ``title`` and (for works) ``abstract`` in the
payload, so rerank works against real text.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Literal

from src.index._rcp.embed_client import (
    RCPEmbeddingClient,
    RCPEmbeddingError,
)
from src.index._rcp.reranker_client import RCPRerankerClient
from src.index.openalex.vector.qdrant_store import (
    PER_ENTITY_COLLECTIONS,
    QdrantStore,
)
from src.v2.ingest.providers._rag_helpers import (
    apply_rerank_indices,
    env_enabled,
    expand_candidate_k,
    filter_allowlist,
    make_snippet,
    safe_rerank_documents,
    thin_payload,
    to_simple_filter_payload,
)

if TYPE_CHECKING:
    from src.index.openalex.config import OpenAlexIndexConfig

logger = logging.getLogger(__name__)

CollectionName = Literal[
    "works", "authors", "institutions", "sources", "topics", "concepts",
]

_ALLOWED_FILTER_KEYS: frozenset[str] = frozenset({
    "year",
    "publication_year",
    "doi",
    "entity_type",
    "openalex_id",
    "country_code",
    "type",
    "field_id",
    "domain_id",
    "level",
    "primary_topic_id",
    "primary_source_id",
    "last_known_institution_id",
    "orcid",
    "ror",
})

_THIN_KEYS_FOR: dict[str, tuple[str, ...]] = {
    "works": (
        "openalex_id", "title", "doi", "year", "publication_year",
        "primary_topic_id", "primary_source_id",
    ),
    "authors": (
        "openalex_id", "display_name", "orcid",
        "last_known_institution_id",
    ),
    "institutions": (
        "openalex_id", "display_name", "ror", "country_code",
    ),
    "sources": (
        "openalex_id", "display_name", "issn_l", "type",
    ),
    "topics": (
        "openalex_id", "display_name", "domain_id", "field_id",
    ),
    "concepts": (
        "openalex_id", "display_name", "level",
    ),
}

_DEFAULT_TOP_K = 10
_LOG_LABEL = "openalex_rag"


def _rerank_text(collection: str, payload: dict[str, Any]) -> str:
    if collection == "works":
        bits = [payload.get("title"), payload.get("abstract")]
        return "\n".join(b for b in bits if isinstance(b, str) and b)
    name = payload.get("display_name") or payload.get("title") or ""
    extra = payload.get("country_code") or payload.get("type") or ""
    return f"{name} {extra}".strip()


class OpenAlexRagProvider:
    """Async wrapper around the OpenAlex Qdrant index."""

    def __init__(
        self,
        *,
        store: QdrantStore,
        embedder: RCPEmbeddingClient,
        reranker: RCPRerankerClient | None = None,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._reranker = reranker

    @classmethod
    def from_config(cls, cfg: OpenAlexIndexConfig) -> OpenAlexRagProvider:
        return cls(
            store=QdrantStore(cfg),
            embedder=RCPEmbeddingClient(cfg),
            reranker=RCPRerankerClient(cfg),
        )

    async def search(  # noqa: PLR0911 — each early return is a graceful-fail check
        self,
        query: str,
        *,
        collection: CollectionName = "works",
        top_k: int = _DEFAULT_TOP_K,
        filters: dict[str, Any] | None = None,
        rerank: bool = False,
    ) -> list[dict[str, Any]]:
        if not isinstance(query, str) or not query.strip():
            return []
        if collection not in PER_ENTITY_COLLECTIONS:
            logger.warning("%s: unknown collection %r", _LOG_LABEL, collection)
            return []
        if not self._store.client.collection_exists(collection):
            logger.info(
                "%s: collection %s missing — returning [] without indexing",
                _LOG_LABEL, collection,
            )
            return []

        cleaned = filter_allowlist(filters, _ALLOWED_FILTER_KEYS, log_label=_LOG_LABEL)
        filter_payload = to_simple_filter_payload(cleaned)

        try:
            vectors = await self._embedder.embed_all([query])
        except (RCPEmbeddingError, Exception) as exc:  # noqa: BLE001
            logger.warning("%s: embed failed — %s", _LOG_LABEL, exc)
            return []
        if not vectors:
            return []
        vector = list(vectors[0])

        candidate_k = expand_candidate_k(top_k) if rerank else top_k

        try:
            hits = await asyncio.to_thread(
                self._store.search,
                collection,
                query_vector=vector,
                top_k=candidate_k,
                filter_payload=filter_payload,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "%s: qdrant search failed (collection=%s) — %s",
                _LOG_LABEL, collection, exc,
            )
            return []

        if rerank and hits and self._reranker is not None:
            hits = await self._maybe_rerank(query, collection, hits, top_k=top_k)
        else:
            hits = hits[:top_k]

        thin_keys = _THIN_KEYS_FOR[collection]
        return [
            thin_payload(
                hit.get("payload") or {},
                thin_keys,
                extras={
                    "id": hit.get("id"),
                    "score": hit.get("score"),
                    "collection": collection,
                    "snippet": (
                        make_snippet((hit.get("payload") or {}).get("abstract"))
                        if collection == "works" else None
                    ),
                },
            )
            for hit in hits
        ]

    async def _maybe_rerank(
        self,
        query: str,
        collection: str,
        hits: list[dict[str, Any]],
        *,
        top_k: int,
    ) -> list[dict[str, Any]]:
        documents = safe_rerank_documents(
            [_rerank_text(collection, h.get("payload") or {}) for h in hits],
        )
        try:
            results = await self._reranker.rerank(query, documents, top_n=top_k)
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s: rerank failed — %s", _LOG_LABEL, exc)
            return hits[:top_k]
        return apply_rerank_indices(hits, results, top_k=top_k)


def build_default_provider(
    cfg: OpenAlexIndexConfig | None = None,
) -> OpenAlexRagProvider | None:
    if not env_enabled("V2_OPENALEX_RAG_ENABLED"):
        return None
    try:
        from src.index.openalex.config import load_config  # noqa: PLC0415

        resolved = cfg or load_config()
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s: failed to load config (%s)", _LOG_LABEL, exc)
        return None
    try:
        return OpenAlexRagProvider.from_config(resolved)
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s: failed to construct provider (%s)", _LOG_LABEL, exc)
        return None


__all__ = [
    "CollectionName",
    "OpenAlexRagProvider",
    "build_default_provider",
]
