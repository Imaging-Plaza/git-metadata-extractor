"""Async RAG provider over the SWISSUbase Qdrant index.

Single Qdrant collection ``swissubase_entities`` covers all four entity
types (``studies``, ``datasets``, ``persons``, ``institutions``) — the
``entity_type`` payload field disambiguates and is allowlisted as a
filter so the LLM can scope the search.

Reuses OpenAlex's embedder, reranker, and ``QdrantStore`` via duck
typing — ``SwissubaseIndexConfig`` mirrors the same ``rcp.*`` /
``qdrant.*`` shape OpenAlex's clients expect.

Every result includes ``source_url`` from the payload (set during
embed) — the project's hard requirement that every entity preserve its
canonical SWISSUbase URL.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from src.index._rcp.embed_client import (
    RCPEmbeddingClient,
    RCPEmbeddingError,
)
from src.index._rcp.reranker_client import RCPRerankerClient
from src.index.openalex.vector.qdrant_store import QdrantStore
from src.index.swissubase.embed.pipeline import SWISSUBASE_COLLECTION
from src.v2.ingest.providers._rag_helpers import (
    apply_rerank_indices,
    env_enabled,
    expand_candidate_k,
    filter_allowlist,
    safe_rerank_documents,
    thin_payload,
    to_simple_filter_payload,
)

if TYPE_CHECKING:
    from src.index.swissubase.config import SwissubaseIndexConfig

logger = logging.getLogger(__name__)

_ALLOWED_FILTER_KEYS: frozenset[str] = frozenset({
    "entity_type",
    "study_id",
    "dataset_id",
    "person_key",
    "institution_key",
    "ref",
    "main_discipline",
    "sub_discipline",
    "progress",
    "year_start",
    "year_end",
    "access_right",
})

_THIN_KEYS: tuple[str, ...] = (
    "entity_type",
    "entity_id",
    "study_id",
    "dataset_id",
    "person_key",
    "institution_key",
    "ref",
    "title",
    "name",
    "display_name",
    "main_discipline",
    "sub_discipline",
    "progress",
    "year_start",
    "year_end",
    "dataset_count",
    "access_right",
    "source_url",
)

_DEFAULT_TOP_K = 10
_LOG_LABEL = "swissubase_rag"


def _rerank_text(payload: dict[str, Any]) -> str:
    title = (
        payload.get("title")
        or payload.get("display_name")
        or payload.get("name")
        or ""
    )
    discipline = payload.get("main_discipline") or ""
    return f"{title}\n{discipline}".strip()


class SwissubaseRagProvider:
    """Async wrapper around the SWISSUbase Qdrant index."""

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
    def from_config(cls, cfg: SwissubaseIndexConfig) -> SwissubaseRagProvider:
        return cls(
            store=QdrantStore(cfg),  # type: ignore[arg-type]
            embedder=RCPEmbeddingClient(cfg),  # type: ignore[arg-type]
            reranker=RCPRerankerClient(cfg),  # type: ignore[arg-type]
        )

    async def search(
        self,
        query: str,
        *,
        top_k: int = _DEFAULT_TOP_K,
        filters: dict[str, Any] | None = None,
        rerank: bool = False,
    ) -> list[dict[str, Any]]:
        if not isinstance(query, str) or not query.strip():
            return []
        if not self._store.client.collection_exists(SWISSUBASE_COLLECTION):
            logger.info(
                "%s: collection %s missing — returning [] without indexing",
                _LOG_LABEL, SWISSUBASE_COLLECTION,
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
                SWISSUBASE_COLLECTION,
                query_vector=vector,
                top_k=candidate_k,
                filter_payload=filter_payload,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s: qdrant search failed — %s", _LOG_LABEL, exc)
            return []

        if rerank and hits and self._reranker is not None:
            hits = await self._maybe_rerank(query, hits, top_k=top_k)
        else:
            hits = hits[:top_k]

        return [
            thin_payload(
                hit.get("payload") or {},
                _THIN_KEYS,
                extras={
                    "id": hit.get("id"),
                    "score": hit.get("score"),
                    "collection": SWISSUBASE_COLLECTION,
                },
            )
            for hit in hits
        ]

    async def _maybe_rerank(
        self,
        query: str,
        hits: list[dict[str, Any]],
        *,
        top_k: int,
    ) -> list[dict[str, Any]]:
        documents = safe_rerank_documents(
            [_rerank_text(h.get("payload") or {}) for h in hits],
        )
        try:
            results = await self._reranker.rerank(query, documents, top_n=top_k)
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s: rerank failed — %s", _LOG_LABEL, exc)
            return hits[:top_k]
        return apply_rerank_indices(hits, results, top_k=top_k)


def build_default_provider(
    cfg: SwissubaseIndexConfig | None = None,
) -> SwissubaseRagProvider | None:
    if not env_enabled("V2_SWISSUBASE_RAG_ENABLED"):
        return None
    try:
        from src.index.swissubase.config import load_config  # noqa: PLC0415

        resolved = cfg or load_config()
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s: failed to load config (%s)", _LOG_LABEL, exc)
        return None
    try:
        return SwissubaseRagProvider.from_config(resolved)
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s: failed to construct provider (%s)", _LOG_LABEL, exc)
        return None


__all__ = ["SwissubaseRagProvider", "build_default_provider"]
