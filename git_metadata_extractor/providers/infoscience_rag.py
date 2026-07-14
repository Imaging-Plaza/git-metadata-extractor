"""Async RAG provider over the Infoscience Qdrant index.

Wraps :class:`open_pulse_sources.index.infoscience.store.QdrantStore` with embedding (via
:class:`open_pulse_sources.index.infoscience.embed.RCPEmbedder`) and optional reranking
(via :class:`open_pulse_sources.index.infoscience.rerank.RCPReranker`).

Exposes three methods used by v2 agent tools:

* ``search(query, *, collection, top_k, filters, rerank)`` — embed → vector
  search → optional rerank. Returns trimmed hits (snippet only).
* ``fetch_chunks(article_uuid, *, max_chunks)`` — pull all chunk bodies for
  one article, ordered by ``chunk_index``.
* ``fetch_records(collection, ids)`` — full payloads for articles / persons
  / organizations by point ID.

Graceful degradation: a connection / RCP failure returns ``[]`` and logs a
warning rather than raising — the agent can fall back to other tools.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Literal

from open_pulse_sources.index.infoscience.embed import EmbedError, RCPEmbedder
from open_pulse_sources.index.infoscience.rerank import RCPReranker, RerankError
from open_pulse_sources.index.infoscience.store import (
    ARTICLES_COLLECTION,
    CHUNKS_COLLECTION,
    ORGANIZATIONS_COLLECTION,
    PERSONS_COLLECTION,
    QdrantStore,
    build_filter,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from open_pulse_sources.index.infoscience.config import InfoscienceIndexConfig

logger = logging.getLogger(__name__)

CollectionName = Literal[
    "chunks",
    "articles",
    "persons",
    "organizations",
]

_COLLECTION_MAP: dict[str, str] = {
    "chunks": CHUNKS_COLLECTION,
    "articles": ARTICLES_COLLECTION,
    "persons": PERSONS_COLLECTION,
    "organizations": ORGANIZATIONS_COLLECTION,
}

# Fields the LLM may filter on. Anything else is dropped with a warning so
# typos / hallucinated keys don't silently match nothing in Qdrant.
_ALLOWED_FILTER_KEYS: frozenset[str] = frozenset({
    "has_github_match",
    "has_hf_match",
    "year",
    "publication_type",
    "language",
    "lab_uuid",
    "org_uuids",
    "doi",
    "orcid",
    "ror_id",
    "sciper_id",
    "sciper_unit_id",
    "author_uuids",
    "subjects",
    "keywords",
})

_SNIPPET_CHARS = 320
_DEFAULT_TOP_K = 10
_DEFAULT_MAX_CHUNKS = 20
_RERANK_CANDIDATE_MULTIPLIER = 5
_RERANK_CANDIDATE_FLOOR = 30


def _resolve_collection(name: str) -> str:
    qdrant_name = _COLLECTION_MAP.get(name)
    if qdrant_name is None:
        msg = (
            f"Unknown infoscience collection {name!r}; "
            f"expected one of {sorted(_COLLECTION_MAP)}"
        )
        raise ValueError(msg)
    return qdrant_name


def _filter_allowlist(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not payload:
        return None
    cleaned: dict[str, Any] = {}
    dropped: list[str] = []
    for key, value in payload.items():
        if key in _ALLOWED_FILTER_KEYS:
            cleaned[key] = value
        else:
            dropped.append(key)
    if dropped:
        logger.warning(
            "infoscience_rag: dropped non-allowlisted filter keys: %s",
            sorted(dropped),
        )
    return cleaned or None


def _snippet(payload: dict[str, Any]) -> str | None:
    text = payload.get("text") or payload.get("abstract")
    if not isinstance(text, str) or not text:
        return None
    text = text.strip()
    if len(text) <= _SNIPPET_CHARS:
        return text
    return text[: _SNIPPET_CHARS - 1].rstrip() + "…"


def _hit_snippet(collection: str, hit: dict[str, Any]) -> dict[str, Any]:
    """Return a thin hit suitable for an LLM tool response.

    Drops bulky fields (full chunk text, raw matched_urls list) but keeps
    everything the agent needs to decide whether to fetch the full record.
    """
    payload = hit.get("payload") or {}
    base: dict[str, Any] = {
        "id": hit.get("id"),
        "score": hit.get("score"),
        "collection": collection,
    }
    if collection == "chunks":
        base.update({
            "article_uuid": payload.get("article_uuid"),
            "chunk_index": payload.get("chunk_index"),
            "title": payload.get("title"),
            "doi": payload.get("doi"),
            "year": payload.get("year"),
            "infoscience_url": payload.get("infoscience_url"),
            "snippet": _snippet(payload),
        })
    elif collection == "articles":
        base.update({
            "article_uuid": payload.get("article_uuid"),
            "title": payload.get("title"),
            "doi": payload.get("doi"),
            "year": payload.get("year"),
            "publication_type": payload.get("publication_type"),
            "authors": payload.get("authors"),
            "infoscience_url": payload.get("infoscience_url"),
            "snippet": _snippet(payload),
        })
    elif collection == "persons":
        base.update({
            "person_uuid": payload.get("person_uuid"),
            "name": payload.get("name"),
            "orcid": payload.get("orcid"),
            "sciper_id": payload.get("sciper_id"),
            "primary_affiliation": payload.get("primary_affiliation"),
            "profile_url": payload.get("profile_url"),
        })
    elif collection == "organizations":
        base.update({
            "org_uuid": payload.get("org_uuid"),
            "name": payload.get("name"),
            "acronym": payload.get("acronym"),
            "ror_id": payload.get("ror_id"),
            "sciper_unit_id": payload.get("sciper_unit_id"),
            "infoscience_url": payload.get("infoscience_url"),
        })
    return {k: v for k, v in base.items() if v is not None}


def _rerank_text(collection: str, payload: dict[str, Any]) -> str:
    if collection == "chunks":
        return (payload.get("text") or payload.get("abstract") or payload.get("title") or "")
    if collection == "articles":
        title = payload.get("title") or ""
        abstract = payload.get("abstract") or ""
        return f"{title}\n{abstract}".strip()
    if collection == "persons":
        return " ".join(filter(None, [
            payload.get("name"),
            payload.get("primary_affiliation"),
            payload.get("biography"),
        ]))
    if collection == "organizations":
        return " ".join(filter(None, [
            payload.get("name"),
            payload.get("acronym"),
            payload.get("description"),
        ]))
    return ""


class InfoscienceRagProvider:
    """Async wrapper around the Infoscience Qdrant index."""

    def __init__(
        self,
        *,
        store: QdrantStore,
        embedder: RCPEmbedder,
        reranker: RCPReranker | None = None,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._reranker = reranker
        self._embedder_open = False
        self._reranker_open = False
        self._lifecycle_lock = asyncio.Lock()

    @classmethod
    def from_config(cls, cfg: InfoscienceIndexConfig) -> InfoscienceRagProvider:
        store = QdrantStore.from_config(cfg)
        embedder = RCPEmbedder(cfg.rcp)
        reranker = RCPReranker(cfg.rcp)
        return cls(store=store, embedder=embedder, reranker=reranker)

    async def aclose(self) -> None:
        async with self._lifecycle_lock:
            if self._embedder_open:
                await self._embedder.__aexit__(None, None, None)
                self._embedder_open = False
            if self._reranker_open and self._reranker is not None:
                await self._reranker.__aexit__(None, None, None)
                self._reranker_open = False

    async def _ensure_embedder(self) -> None:
        if self._embedder_open:
            return
        async with self._lifecycle_lock:
            if not self._embedder_open:
                await self._embedder.__aenter__()
                self._embedder_open = True

    async def _ensure_reranker(self) -> bool:
        if self._reranker is None:
            return False
        if self._reranker_open:
            return True
        async with self._lifecycle_lock:
            if not self._reranker_open:
                await self._reranker.__aenter__()
                self._reranker_open = True
        return True

    async def search(
        self,
        query: str,
        *,
        collection: CollectionName = "chunks",
        top_k: int = _DEFAULT_TOP_K,
        filters: dict[str, Any] | None = None,
        rerank: bool = False,
    ) -> list[dict[str, Any]]:
        if not isinstance(query, str) or not query.strip():
            return []
        qdrant_collection = _resolve_collection(collection)
        cleaned_filters = _filter_allowlist(filters)
        try:
            qdrant_filter = build_filter(cleaned_filters)
        except ValueError as exc:
            logger.warning("infoscience_rag: invalid filter %r — %s", filters, exc)
            return []

        try:
            await self._ensure_embedder()
            vector = await self._embedder.embed_query(query)
        except (EmbedError, RuntimeError) as exc:
            logger.warning("infoscience_rag: embed failed — %s", exc)
            return []

        candidate_k = top_k
        if rerank:
            candidate_k = max(top_k * _RERANK_CANDIDATE_MULTIPLIER, _RERANK_CANDIDATE_FLOOR)

        try:
            hits = await asyncio.to_thread(
                self._store.search,
                qdrant_collection,
                query_vector=vector,
                top_k=candidate_k,
                query_filter=qdrant_filter,
            )
        except Exception as exc:  # noqa: BLE001 — Qdrant client raises a wide set
            logger.warning(
                "infoscience_rag: qdrant search failed (collection=%s) — %s",
                qdrant_collection, exc,
            )
            return []

        if rerank and hits:
            hits = await self._maybe_rerank(query, collection, hits, top_k=top_k)
        else:
            hits = hits[:top_k]

        return [_hit_snippet(collection, h) for h in hits]

    async def _maybe_rerank(
        self,
        query: str,
        collection: str,
        hits: list[dict[str, Any]],
        *,
        top_k: int,
    ) -> list[dict[str, Any]]:
        try:
            available = await self._ensure_reranker()
        except RerankError as exc:
            logger.warning("infoscience_rag: reranker open failed — %s", exc)
            return hits[:top_k]
        if not available or self._reranker is None:
            return hits[:top_k]

        documents = [_rerank_text(collection, h.get("payload") or {}) for h in hits]
        try:
            ranked = await self._reranker.rerank(query, documents, top_n=top_k)
        except RerankError as exc:
            logger.warning("infoscience_rag: rerank failed — %s", exc)
            return hits[:top_k]

        if not ranked:
            return hits[:top_k]
        ordered: list[dict[str, Any]] = []
        seen: set[int] = set()
        for hit in ranked:
            if 0 <= hit.index < len(hits) and hit.index not in seen:
                seen.add(hit.index)
                rehit = dict(hits[hit.index])
                rehit["score"] = float(hit.score)
                ordered.append(rehit)
        return ordered[:top_k]

    async def fetch_chunks(
        self,
        article_uuid: str,
        *,
        max_chunks: int = _DEFAULT_MAX_CHUNKS,
    ) -> list[dict[str, Any]]:
        if not isinstance(article_uuid, str) or not article_uuid.strip():
            return []
        try:
            qdrant_filter = build_filter({"article_uuid": article_uuid})
        except ValueError as exc:
            logger.warning("infoscience_rag: bad article_uuid filter — %s", exc)
            return []
        try:
            records = await asyncio.to_thread(
                self._store.scroll,
                CHUNKS_COLLECTION,
                query_filter=qdrant_filter,
                limit=max_chunks,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "infoscience_rag: qdrant scroll failed (article_uuid=%s) — %s",
                article_uuid, exc,
            )
            return []

        chunks: list[dict[str, Any]] = []
        for rec in records:
            payload = rec.get("payload") or {}
            chunks.append({
                "id": rec.get("id"),
                "article_uuid": payload.get("article_uuid"),
                "chunk_index": payload.get("chunk_index"),
                "text": payload.get("text"),
                "title": payload.get("title"),
                "doi": payload.get("doi"),
                "infoscience_url": payload.get("infoscience_url"),
            })
        chunks.sort(key=lambda c: (c.get("chunk_index") if isinstance(c.get("chunk_index"), int) else 0))
        return chunks

    async def fetch_records(
        self,
        collection: CollectionName,
        ids: Sequence[str],
    ) -> list[dict[str, Any]]:
        if collection == "chunks":
            msg = "Use fetch_chunks(article_uuid) for chunk bodies, not fetch_records."
            raise ValueError(msg)
        qdrant_collection = _resolve_collection(collection)
        clean_ids = [i for i in ids if isinstance(i, str) and i]
        if not clean_ids:
            return []
        try:
            records = await asyncio.to_thread(
                self._store.lookup,
                qdrant_collection,
                ids=clean_ids,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "infoscience_rag: qdrant lookup failed (collection=%s) — %s",
                qdrant_collection, exc,
            )
            return []
        return [
            {"id": rec.get("id"), "payload": rec.get("payload") or {}}
            for rec in records
        ]


def _truthy_env(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "t", "yes", "y", "on"}


def _falsy_env(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"0", "false", "f", "no", "n", "off"}


def build_default_provider(
    cfg: InfoscienceIndexConfig | None = None,
) -> InfoscienceRagProvider | None:
    """Construct the default RAG provider from on-disk config.

    Returns ``None`` (not raising) when the config or its dependencies fail
    to load, so the rest of the v2 pipeline keeps working without RAG.
    """
    # Inline import: lazy load avoids hard-failing v2 init when the
    # infoscience yaml or its env vars are missing in non-RAG deployments.
    try:
        from open_pulse_sources.index.infoscience.config import load_config  # noqa: PLC0415

        resolved = cfg or load_config()
    except Exception as exc:  # noqa: BLE001 — config errors must not 500 the API
        logger.warning(
            "infoscience_rag: failed to load index config; tools disabled (%s)",
            exc,
        )
        return None
    try:
        return InfoscienceRagProvider.from_config(resolved)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "infoscience_rag: failed to construct provider; tools disabled (%s)",
            exc,
        )
        return None


__all__ = [
    "CollectionName",
    "InfoscienceRagProvider",
    "build_default_provider",
]
