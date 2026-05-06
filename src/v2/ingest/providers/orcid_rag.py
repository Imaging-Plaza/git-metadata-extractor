"""Async RAG provider over the ORCID Qdrant index.

Three per-(scope, entity) collections (named ``orcid_<scope>_<entity>``):
``persons`` / ``employments`` / ``educations``. The active scope (e.g.
``epfl`` or ``switzerland``) is determined by ``OrcidIndexConfig.paths``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Literal

from src.index.openalex.embed.rcp_client import RCPEmbeddingError
from src.index.orcid.embed.rcp_client import RCPEmbeddingClient
from src.index.orcid.rerank.rcp_client import RCPRerankerClient
from src.index.orcid.vector.qdrant_store import ENTITY_TYPES, OrcidQdrantStore
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
    from src.index.orcid.config import OrcidIndexConfig

logger = logging.getLogger(__name__)

EntityType = Literal["persons", "employments", "educations"]

_ALLOWED_FILTER_KEYS: frozenset[str] = frozenset({
    "orcid_id",
    "in_scope",
    "discovered_via",
    "org_ror",
    "organization",
    "department",
    "role",
})

_THIN_KEYS_FOR: dict[str, tuple[str, ...]] = {
    "persons": (
        "orcid_id", "display_name", "given_name", "family_name",
        "in_scope", "discovered_via",
    ),
    "employments": (
        "orcid_id", "organization", "org_ror", "department",
        "role", "start_date", "end_date",
    ),
    "educations": (
        "orcid_id", "organization", "org_ror", "department",
        "role", "start_date", "end_date",
    ),
}

_DEFAULT_TOP_K = 10
_LOG_LABEL = "orcid_rag"


def _rerank_text(entity_type: str, payload: dict[str, Any]) -> str:
    if entity_type == "persons":
        bits = [
            payload.get("display_name"),
            payload.get("biography"),
        ]
    else:
        bits = [
            payload.get("organization"),
            payload.get("department"),
            payload.get("role"),
        ]
    return " ".join(b for b in bits if isinstance(b, str) and b)


class OrcidRagProvider:
    """Async wrapper around the ORCID Qdrant index."""

    def __init__(
        self,
        *,
        store: OrcidQdrantStore,
        embedder: RCPEmbeddingClient,
        reranker: RCPRerankerClient | None = None,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._reranker = reranker

    @classmethod
    def from_config(cls, cfg: OrcidIndexConfig) -> OrcidRagProvider:
        return cls(
            store=OrcidQdrantStore(cfg),
            embedder=RCPEmbeddingClient(cfg),
            reranker=RCPRerankerClient(cfg),
        )

    async def search(  # noqa: PLR0911 — each early return is a graceful-fail check
        self,
        query: str,
        *,
        entity_type: EntityType = "persons",
        top_k: int = _DEFAULT_TOP_K,
        filters: dict[str, Any] | None = None,
        rerank: bool = False,
    ) -> list[dict[str, Any]]:
        if not isinstance(query, str) or not query.strip():
            return []
        if entity_type not in ENTITY_TYPES:
            logger.warning("%s: unknown entity_type %r", _LOG_LABEL, entity_type)
            return []

        # Skip the call entirely if the indexed collection doesn't exist —
        # the upstream store would otherwise lazily *create* it.
        collection_name = self._store.collection(entity_type)
        if not self._store.client.collection_exists(collection_name):
            logger.info(
                "%s: collection %s missing — returning [] without indexing",
                _LOG_LABEL, collection_name,
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
                entity_type,
                query_vector=vector,
                top_k=candidate_k,
                filter_payload=filter_payload,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "%s: qdrant search failed (entity=%s) — %s",
                _LOG_LABEL, entity_type, exc,
            )
            return []

        if rerank and hits and self._reranker is not None:
            hits = await self._maybe_rerank(query, entity_type, hits, top_k=top_k)
        else:
            hits = hits[:top_k]

        thin_keys = _THIN_KEYS_FOR[entity_type]
        return [
            thin_payload(
                hit.get("payload") or {},
                thin_keys,
                extras={
                    "id": hit.get("id"),
                    "score": hit.get("score"),
                    "entity_type": entity_type,
                    "snippet": (
                        make_snippet((hit.get("payload") or {}).get("biography"))
                        if entity_type == "persons" else None
                    ),
                },
            )
            for hit in hits
        ]

    async def _maybe_rerank(
        self,
        query: str,
        entity_type: str,
        hits: list[dict[str, Any]],
        *,
        top_k: int,
    ) -> list[dict[str, Any]]:
        documents = safe_rerank_documents(
            [_rerank_text(entity_type, h.get("payload") or {}) for h in hits],
        )
        try:
            results = await self._reranker.rerank(query, documents, top_n=top_k)
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s: rerank failed — %s", _LOG_LABEL, exc)
            return hits[:top_k]
        return apply_rerank_indices(hits, results, top_k=top_k)


def build_default_provider(
    cfg: OrcidIndexConfig | None = None,
) -> OrcidRagProvider | None:
    if not env_enabled("V2_ORCID_RAG_ENABLED"):
        return None
    try:
        from src.index.orcid.config import load_config  # noqa: PLC0415

        resolved = cfg or load_config()
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s: failed to load config (%s)", _LOG_LABEL, exc)
        return None
    try:
        return OrcidRagProvider.from_config(resolved)
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s: failed to construct provider (%s)", _LOG_LABEL, exc)
        return None


__all__ = ["EntityType", "OrcidRagProvider", "build_default_provider"]
