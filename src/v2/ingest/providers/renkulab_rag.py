"""Async RAG provider over the RenkuLab Qdrant index.

Four collections (one per entity type): ``renkulab_projects``,
``renkulab_groups``, ``renkulab_users``, ``renkulab_data_connectors``.
Reuses OpenAlex's embedder, reranker and ``QdrantStore`` via duck typing
— ``RenkulabIndexConfig`` mirrors the same ``rcp.*`` / ``qdrant.*`` shape
those clients expect.
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
from src.index.renkulab.embed.pipeline import COLLECTION_BY_ENTITY
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
    from src.index.renkulab.config import RenkulabIndexConfig

logger = logging.getLogger(__name__)

_ALLOWED_FILTER_KEYS: frozenset[str] = frozenset({
    "entity_type",
    "slug",
    "namespace",
    "path",
    "visibility",
    "storage_type",
    "storage_provider",
    "entity_id",
})

# Per-entity thin-payload key sets — projection happens after the
# entity_type discriminator is known.
_THIN_KEYS_BY_ENTITY: dict[str, tuple[str, ...]] = {
    "projects": ("entity_id", "name", "slug", "namespace", "path", "visibility"),
    "groups": ("entity_id", "name", "slug"),
    "users": ("entity_id", "first_name", "last_name", "slug", "path"),
    "data_connectors": (
        "entity_id", "name", "slug", "namespace", "path",
        "storage_type", "storage_provider", "visibility",
    ),
}

_DEFAULT_TOP_K = 10
_LOG_LABEL = "renkulab_rag"

_RENKU_BASE = "https://renkulab.io/v2"


def _build_url(entity_type: str, payload: dict[str, Any]) -> str | None:
    path = payload.get("path") or payload.get("slug")
    if entity_type == "projects" and path:
        return f"{_RENKU_BASE}/projects/{path}"
    if entity_type == "groups" and path:
        return f"{_RENKU_BASE}/groups/{path}"
    if entity_type == "users" and path:
        return f"{_RENKU_BASE}/users/{path}"
    if entity_type == "data_connectors" and path:
        return f"{_RENKU_BASE}/data-connectors/{path}"
    return None


def _rerank_text(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("name", "first_name", "last_name", "namespace", "path", "slug",
                "storage_type", "storage_provider"):
        v = payload.get(key)
        if v:
            parts.append(str(v))
    return " ".join(parts).strip()


def _resolve_target_collections(
    entity_types: list[str] | None,
) -> list[tuple[str, str]]:
    if not entity_types:
        return [(et, COLLECTION_BY_ENTITY[et]) for et in COLLECTION_BY_ENTITY]
    out: list[tuple[str, str]] = []
    for et in entity_types:
        if et in COLLECTION_BY_ENTITY:
            out.append((et, COLLECTION_BY_ENTITY[et]))
        else:
            logger.warning(
                "%s: ignoring unknown entity_type %r — known: %s",
                _LOG_LABEL, et, sorted(COLLECTION_BY_ENTITY),
            )
    return out


class RenkulabRagProvider:
    """Async wrapper around the RenkuLab Qdrant index (4 collections)."""

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
    def from_config(cls, cfg: RenkulabIndexConfig) -> RenkulabRagProvider:
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
        entity_types: list[str] | None = None,
        filters: dict[str, Any] | None = None,
        rerank: bool = False,
    ) -> list[dict[str, Any]]:
        if not isinstance(query, str) or not query.strip():
            return []
        targets = _resolve_target_collections(entity_types)
        if not targets:
            return []

        cleaned = filter_allowlist(filters, _ALLOWED_FILTER_KEYS, log_label=_LOG_LABEL)
        filter_payload = to_simple_filter_payload(cleaned)
        vector = await self._embed_query(query)
        if vector is None:
            return []

        candidate_k = expand_candidate_k(top_k) if rerank else top_k
        all_hits = await self._gather_candidates(
            vector=vector,
            targets=targets,
            candidate_k=candidate_k,
            filter_payload=filter_payload,
        )
        if not all_hits:
            return []

        all_hits.sort(key=lambda h: float(h.get("score") or 0.0), reverse=True)
        all_hits = all_hits[: max(candidate_k, top_k)]

        if rerank and self._reranker is not None:
            all_hits = await self._maybe_rerank(query, all_hits, top_k=top_k)
        else:
            all_hits = all_hits[:top_k]

        return [self._project_hit(h) for h in all_hits]

    async def _embed_query(self, query: str) -> list[float] | None:
        try:
            vectors = await self._embedder.embed_all([query])
        except (RCPEmbeddingError, Exception) as exc:  # noqa: BLE001
            logger.warning("%s: embed failed — %s", _LOG_LABEL, exc)
            return None
        if not vectors:
            return None
        return list(vectors[0])

    async def _gather_candidates(
        self,
        *,
        vector: list[float],
        targets: list[tuple[str, str]],
        candidate_k: int,
        filter_payload: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for entity_type, collection in targets:
            if not self._store.client.collection_exists(collection):
                logger.info(
                    "%s: collection %s missing — skipping",
                    _LOG_LABEL, collection,
                )
                continue
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
                    "%s: qdrant search failed for %s — %s",
                    _LOG_LABEL, collection, exc,
                )
                continue
            for hit in hits:
                payload = dict(hit.get("payload") or {})
                payload.setdefault("entity_type", entity_type)
                out.append({**hit, "payload": payload, "_collection": collection})
        return out

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

    @staticmethod
    def _project_hit(hit: dict[str, Any]) -> dict[str, Any]:
        payload = hit.get("payload") or {}
        entity_type = str(payload.get("entity_type") or "")
        keys = _THIN_KEYS_BY_ENTITY.get(entity_type, ())
        return thin_payload(
            payload,
            keys,
            extras={
                "id": hit.get("id"),
                "score": hit.get("score"),
                "entity_type": entity_type or None,
                "collection": hit.get("_collection"),
                "url": _build_url(entity_type, payload),
            },
        )


def build_default_provider(
    cfg: RenkulabIndexConfig | None = None,
) -> RenkulabRagProvider | None:
    if not env_enabled("V2_RENKULAB_RAG_ENABLED"):
        return None
    try:
        from src.index.renkulab.config import load_config  # noqa: PLC0415

        resolved = cfg or load_config()
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s: failed to load config (%s)", _LOG_LABEL, exc)
        return None
    try:
        return RenkulabRagProvider.from_config(resolved)
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s: failed to construct provider (%s)", _LOG_LABEL, exc)
        return None


__all__ = ["RenkulabRagProvider", "build_default_provider"]
