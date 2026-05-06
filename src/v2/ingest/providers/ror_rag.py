"""Async RAG provider over the ROR Qdrant index.

Per-scope collections (``ror_epfl_ethz``, ``ror_switzerland``, ``ror_europe``,
``ror_worldwide``). Different shape from the other RAG providers:

* Embedder is a module-level async function (``embed_query(rcp, text)``)
  rather than a class.
* Reranker is also module-level (``rerank(rcp, query, docs, top_n)``).
* The store's ``search`` only supports a single optional ``country`` filter
  (no generic JSON-style filter dict). We translate ``country_code`` from
  the LLM's filters dict and drop the rest with a warning.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Literal

import numpy as np

from src.index.ror.embed import EmbeddingError, embed_query
from src.index.ror.qdrant_store import QdrantRorStore
from src.index.ror.rerank import RerankError, rerank
from src.v2.ingest.providers._rag_helpers import (
    apply_rerank_indices,
    env_enabled,
    expand_candidate_k,
    make_snippet,
    safe_rerank_documents,
)

if TYPE_CHECKING:
    from src.index.ror.config import RcpConfig, RorIndexConfig

logger = logging.getLogger(__name__)

ScopeMode = Literal["epfl_ethz", "switzerland", "europe", "worldwide"]
_KNOWN_SCOPE_MODES: frozenset[str] = frozenset({
    "epfl_ethz", "switzerland", "europe", "worldwide",
})

_THIN_KEYS: tuple[str, ...] = ()  # ROR hits use a custom shape — see _to_thin_hit

_DEFAULT_TOP_K = 10
_LOG_LABEL = "ror_rag"


def _extract_country(filters: dict[str, Any] | None) -> tuple[str | None, list[str]]:
    """Extract ``country_code`` from filters; return (country, dropped_keys)."""
    if not filters:
        return None, []
    country = None
    dropped: list[str] = []
    for key, value in filters.items():
        if key == "country_code":
            if isinstance(value, str) and value.strip():
                country = value.strip()
            elif isinstance(value, dict) and isinstance(value.get("$eq"), str):
                country = value["$eq"].strip()
            else:
                dropped.append(key)
        else:
            dropped.append(key)
    if dropped:
        logger.warning(
            "%s: dropped non-allowlisted filter keys (only country_code "
            "supported by the ROR store): %s",
            _LOG_LABEL, sorted(dropped),
        )
    return country, dropped


def _to_thin_hit(scope_mode: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Build the LLM-facing hit dict from a raw QdrantRorStore.search result."""
    record = raw.get("record") or {}
    types = record.get("types") if isinstance(record, dict) else None
    locations = record.get("locations") if isinstance(record, dict) else None
    country = None
    if isinstance(locations, list) and locations:
        loc = locations[0] or {}
        country = (
            (loc.get("geonames_details") or {}).get("country_code")
            if isinstance(loc, dict) else None
        )
    out: dict[str, Any] = {
        "ror_id": raw.get("ror_id"),
        "name": raw.get("name"),
        "score": raw.get("score"),
        "scope_mode": scope_mode,
        "country_code": country,
        "types": types,
        "snippet": make_snippet(raw.get("text")),
    }
    return {k: v for k, v in out.items() if v is not None}


class RorRagProvider:
    """Async wrapper around the ROR Qdrant index."""

    def __init__(
        self,
        *,
        store: QdrantRorStore,
        rcp: RcpConfig,
    ) -> None:
        self._store = store
        self._rcp = rcp

    @classmethod
    def from_config(cls, cfg: RorIndexConfig) -> RorRagProvider:
        return cls(store=QdrantRorStore(cfg), rcp=cfg.rcp)

    async def search(  # noqa: PLR0911 — each early return is a graceful-fail check
        self,
        query: str,
        *,
        scope_mode: ScopeMode = "worldwide",
        top_k: int = _DEFAULT_TOP_K,
        filters: dict[str, Any] | None = None,
        rerank: bool = False,
    ) -> list[dict[str, Any]]:
        if not isinstance(query, str) or not query.strip():
            return []
        if scope_mode not in _KNOWN_SCOPE_MODES:
            logger.warning("%s: unknown scope_mode %r", _LOG_LABEL, scope_mode)
            return []

        # Skip if the per-scope collection is missing (e.g. only worldwide
        # is built). The upstream store raises FileNotFoundError otherwise.
        collection_name = self._store.collection_name(scope_mode)
        if not self._store.client.collection_exists(collection_name):
            logger.info(
                "%s: collection %s missing — returning [] without indexing",
                _LOG_LABEL, collection_name,
            )
            return []

        country, _dropped = _extract_country(filters)

        try:
            vector_np = await embed_query(self._rcp, query)
        except EmbeddingError as exc:
            logger.warning("%s: embed failed — %s", _LOG_LABEL, exc)
            return []
        if not isinstance(vector_np, np.ndarray):
            logger.warning("%s: embed returned non-array %r", _LOG_LABEL, type(vector_np))
            return []
        vector = vector_np.tolist()

        candidate_k = expand_candidate_k(top_k) if rerank else top_k

        try:
            raw_hits = await asyncio.to_thread(
                self._store.search,
                scope_mode,
                query_vector=vector,
                top_k=candidate_k,
                country=country,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "%s: qdrant search failed (scope=%s) — %s",
                _LOG_LABEL, scope_mode, exc,
            )
            return []

        if rerank and raw_hits:
            raw_hits = await self._maybe_rerank(query, raw_hits, top_k=top_k)
        else:
            raw_hits = raw_hits[:top_k]

        return [_to_thin_hit(scope_mode, h) for h in raw_hits]

    async def _maybe_rerank(
        self,
        query: str,
        hits: list[dict[str, Any]],
        *,
        top_k: int,
    ) -> list[dict[str, Any]]:
        documents = safe_rerank_documents(
            [(h.get("text") or h.get("name") or "") for h in hits],
        )
        try:
            results = await rerank(self._rcp, query, documents, top_n=top_k)
        except RerankError as exc:
            logger.warning("%s: rerank failed — %s", _LOG_LABEL, exc)
            return hits[:top_k]
        # Adapt RerankResult objects to the (index, relevance_score) dict
        # shape apply_rerank_indices expects.
        adapter = [{"index": r.index, "relevance_score": r.score} for r in results]
        # apply_rerank_indices preserves the existing `score` shape; for ROR
        # we simply replace it with the rerank score in-place.
        return apply_rerank_indices(hits, adapter, top_k=top_k)


def build_default_provider(
    cfg: RorIndexConfig | None = None,
) -> RorRagProvider | None:
    if not env_enabled("V2_ROR_RAG_ENABLED"):
        return None
    try:
        from src.index.ror.config import load_config  # noqa: PLC0415

        resolved = cfg or load_config()
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s: failed to load config (%s)", _LOG_LABEL, exc)
        return None
    try:
        return RorRagProvider.from_config(resolved)
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s: failed to construct provider (%s)", _LOG_LABEL, exc)
        return None


__all__ = ["RorRagProvider", "ScopeMode", "build_default_provider"]
