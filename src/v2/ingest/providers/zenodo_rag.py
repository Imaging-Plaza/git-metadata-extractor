"""Async RAG provider over the Zenodo Qdrant index.

Single collection: ``zenodo_records``. Reuses OpenAlex's embedder, reranker
and ``QdrantStore`` via duck typing — ``ZenodoIndexConfig`` mirrors the
same ``rcp.*`` / ``qdrant.*`` shape OpenAlex's clients expect.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from src.index.openalex.embed.rcp_client import (
    RCPEmbeddingClient,
    RCPEmbeddingError,
)
from src.index.openalex.rerank.rcp_client import RCPRerankerClient
from src.index.openalex.vector.qdrant_store import QdrantStore
from src.index.zenodo_records.embed.pipeline import ZENODO_COLLECTION
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
    from collections.abc import Sequence

    from src.index.zenodo_records.config import ZenodoIndexConfig

logger = logging.getLogger(__name__)

_FETCH_RECORDS_DESCRIPTION_KEYS: tuple[str, ...] = (
    "zenodo_id", "concept_recid", "title", "doi", "publication_date",
    "resource_type", "access_right", "license_id", "description",
)
_DESCRIPTION_CAP_CHARS = 2000

_ALLOWED_FILTER_KEYS: frozenset[str] = frozenset({
    "year",
    "doi",
    "resource_type",
    "access_right",
    "entity_type",
    "zenodo_id",
})

_THIN_KEYS: tuple[str, ...] = (
    "zenodo_id", "title", "doi", "year", "resource_type", "access_right",
)

_DEFAULT_TOP_K = 10
_LOG_LABEL = "zenodo_rag"


def _rerank_text(payload: dict[str, Any]) -> str:
    title = payload.get("title") or ""
    desc = payload.get("description") or ""
    return f"{title}\n{desc}".strip()


class ZenodoRagProvider:
    """Async wrapper around the Zenodo Qdrant index."""

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
    def from_config(cls, cfg: ZenodoIndexConfig) -> ZenodoRagProvider:
        # Duck-type into OpenAlex's clients — config shape matches.
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
        if not self._store.client.collection_exists(ZENODO_COLLECTION):
            logger.info(
                "%s: collection %s missing — returning [] without indexing",
                _LOG_LABEL, ZENODO_COLLECTION,
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
                ZENODO_COLLECTION,
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
                    "collection": "zenodo_records",
                },
            )
            for hit in hits
        ]

    async def fetch_records(
        self,
        ids: Sequence[str],
    ) -> list[dict[str, Any]]:
        """Hydrate Zenodo records from DuckDB by ``zenodo_id`` or ``concept_recid``.

        Used by the LLM agent tool to expand thin search hits into full
        records (title, DOI, description, license, ...). Unlike Infoscience
        — which stores rich payloads on Qdrant points — Zenodo's full
        record body lives in DuckDB, so we go to the store rather than
        Qdrant. Falls back to the latest version under a concept_recid
        when the requested ID is the parent (citation) ID.
        """
        clean_ids = [
            i.strip() for i in ids
            if isinstance(i, str) and i.strip()
        ]
        if not clean_ids:
            return []
        return await asyncio.to_thread(self._fetch_records_sync, clean_ids)

    @staticmethod
    def _fetch_records_sync(ids: list[str]) -> list[dict[str, Any]]:
        from src.index.zenodo_records.storage.duckdb_store import (  # noqa: PLC0415
            ZenodoRecordsStore,
        )

        try:
            store = ZenodoRecordsStore.open()
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s: cannot open store — %s", _LOG_LABEL, exc)
            return []
        try:
            out: list[dict[str, Any]] = []
            for rid in ids:
                row = store.fetch_record(rid)
                if row is None:
                    row = store.fetch_record_by_concept(rid)
                if row is None:
                    continue
                description = row.get("description") or ""
                out.append({
                    "zenodo_id": row.get("zenodo_id"),
                    "concept_recid": row.get("concept_recid"),
                    "title": row.get("title"),
                    "doi": row.get("doi"),
                    "publication_date": (
                        row.get("publication_date").isoformat()
                        if hasattr(row.get("publication_date"), "isoformat")
                        else row.get("publication_date")
                    ),
                    "resource_type": row.get("resource_type"),
                    "access_right": row.get("access_right"),
                    "license_id": row.get("license_id"),
                    "description": description[:_DESCRIPTION_CAP_CHARS],
                })
            return out
        finally:
            store.close()

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
    cfg: ZenodoIndexConfig | None = None,
) -> ZenodoRagProvider | None:
    if not env_enabled("V2_ZENODO_RAG_ENABLED"):
        return None
    try:
        from src.index.zenodo_records.config import load_config  # noqa: PLC0415

        resolved = cfg or load_config()
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s: failed to load config (%s)", _LOG_LABEL, exc)
        return None
    try:
        return ZenodoRagProvider.from_config(resolved)
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s: failed to construct provider (%s)", _LOG_LABEL, exc)
        return None


__all__ = ["ZenodoRagProvider", "build_default_provider"]
