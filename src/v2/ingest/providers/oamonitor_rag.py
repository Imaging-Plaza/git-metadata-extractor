"""Async RAG provider over the OAM-CH per-entity Qdrant collections.

Four collections — one per OAM entity table (``oamonitor_journals``,
``oamonitor_publications``, ``oamonitor_publishers``,
``oamonitor_organisations``). The ``entity_type`` argument on
:meth:`search` picks the collection. Reuses OpenAlex's embedder /
reranker / ``QdrantStore`` via duck typing — ``OamonitorIndexConfig``
mirrors the same ``rcp.*`` / ``qdrant.*`` shape OpenAlex's clients expect.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from src.index.oamonitor.embed.pipeline import OAM_COLLECTIONS, qdrant_collection_for
from src.index._rcp.embed_client import (
    RCPEmbeddingClient,
    RCPEmbeddingError,
)
from src.index._rcp.reranker_client import RCPRerankerClient
from src.index.openalex.vector.qdrant_store import QdrantStore
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

    from src.index.oamonitor.config import OamonitorIndexConfig

logger = logging.getLogger(__name__)

_VALID_ENTITIES: frozenset[str] = frozenset(OAM_COLLECTIONS.keys())
_DEFAULT_ENTITY = "journals"
_DEFAULT_TOP_K = 10
_LOG_LABEL = "oamonitor_rag"

_ALLOWED_FILTER_KEYS: frozenset[str] = frozenset({
    "entity_type", "entity_id",
})

_THIN_KEYS: tuple[str, ...] = (
    "entity_type", "entity_id", "embedding_text",
)


def _rerank_text(payload: dict[str, Any]) -> str:
    text = payload.get("embedding_text") or ""
    return str(text).strip()


class OamonitorRagProvider:
    """Async wrapper around the OAM-CH per-entity Qdrant collections."""

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
    def from_config(cls, cfg: OamonitorIndexConfig) -> OamonitorRagProvider:
        return cls(
            store=QdrantStore(cfg),  # type: ignore[arg-type]
            embedder=RCPEmbeddingClient(cfg),  # type: ignore[arg-type]
            reranker=RCPRerankerClient(cfg),  # type: ignore[arg-type]
        )

    async def search(
        self,
        query: str,
        *,
        entity_type: str = _DEFAULT_ENTITY,
        top_k: int = _DEFAULT_TOP_K,
        filters: dict[str, Any] | None = None,
        rerank: bool = False,
    ) -> list[dict[str, Any]]:
        if not isinstance(query, str) or not query.strip():
            return []
        if entity_type not in _VALID_ENTITIES:
            logger.info(
                "%s: unknown entity_type=%r — returning []", _LOG_LABEL, entity_type,
            )
            return []
        collection = qdrant_collection_for(entity_type)
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
                    "collection": collection,
                },
            )
            for hit in hits
        ]

    async def fetch_records(
        self,
        ids: Sequence[str],
        *,
        entity_type: str,
    ) -> list[dict[str, Any]]:
        """Hydrate full OAM rows from DuckDB by ``_id`` for the given entity table."""
        if entity_type not in _VALID_ENTITIES:
            return []
        clean_ids = [
            i.strip() for i in ids
            if isinstance(i, str) and i.strip()
        ]
        if not clean_ids:
            return []
        return await asyncio.to_thread(
            self._fetch_records_sync, clean_ids, entity_type,
        )

    @staticmethod
    def _fetch_records_sync(
        ids: list[str], entity_type: str,
    ) -> list[dict[str, Any]]:
        from src.index.oamonitor.storage.duckdb_store import (  # noqa: PLC0415
            ENTITY_TABLES,
            OamonitorStore,
        )

        if entity_type not in ENTITY_TABLES:
            return []
        try:
            store = OamonitorStore.open()
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s: cannot open store — %s", _LOG_LABEL, exc)
            return []
        try:
            placeholders = ",".join(["?"] * len(ids))
            cursor = store.connect().execute(
                f"SELECT _id, raw FROM {entity_type} "  # noqa: S608
                f"WHERE _id IN ({placeholders})",
                ids,
            )
            out: list[dict[str, Any]] = []
            for row in cursor.fetchall():
                _id, raw = row[0], row[1]
                import json  # noqa: PLC0415

                payload: dict[str, Any]
                if isinstance(raw, str):
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        payload = {}
                elif isinstance(raw, dict):
                    payload = raw
                else:
                    payload = {}
                out.append({"_id": _id, "entity_type": entity_type, "raw": payload})
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
    cfg: OamonitorIndexConfig | None = None,
) -> OamonitorRagProvider | None:
    if not env_enabled("V2_OAMONITOR_RAG_ENABLED"):
        return None
    try:
        from src.index.oamonitor.config import load_config  # noqa: PLC0415

        resolved = cfg or load_config()
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s: failed to load config (%s)", _LOG_LABEL, exc)
        return None
    try:
        return OamonitorRagProvider.from_config(resolved)
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s: failed to construct provider (%s)", _LOG_LABEL, exc)
        return None


__all__ = ["OamonitorRagProvider", "build_default_provider"]
