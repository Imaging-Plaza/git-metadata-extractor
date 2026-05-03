"""Async RAG provider over the SNSF Qdrant index.

Per-scope collections (``snsf_epfl``, ``snsf_ethz``, ``snsf_switzerland``).
Mirrors :mod:`src.v2.ingest.providers.ror_rag` shape:

* Embedder is a module-level async function (``embed_query(rcp, text)``).
* Reranker is also module-level (``rerank(rcp, query, docs, top_n)``).
* The store's ``search`` accepts ``institution`` / ``discipline_l1`` /
  ``state`` payload filters; we forward the ones the LLM passes and drop
  the rest with a warning.

Adds one SNSF-specific affordance: ``institute`` (specific lab/centre name)
isn't in the Qdrant payload, so it's resolved from DuckDB after the ANN —
useful for SDSC-style queries (``--institute "Swiss Data Science Center"``).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Literal, Optional

import numpy as np

from src.index.snsf.embed import EmbeddingError, embed_query
from src.index.snsf.qdrant_store import QdrantSnsfStore
from src.index.snsf.rerank import RerankError, rerank
from src.v2.ingest.providers._rag_helpers import (
    apply_rerank_indices,
    env_enabled,
    expand_candidate_k,
    make_snippet,
    safe_rerank_documents,
)

if TYPE_CHECKING:
    from src.index.snsf.config import RcpConfig, SnsfIndexConfig

logger = logging.getLogger(__name__)

ScopeMode = Literal["epfl", "ethz", "eth_domain", "switzerland"]
_KNOWN_SCOPE_MODES: frozenset[str] = frozenset({
    "epfl", "ethz", "eth_domain", "switzerland",
})

_ALLOWLISTED_FILTERS: frozenset[str] = frozenset({
    "institution", "institute", "discipline_l1", "state",
})

_DEFAULT_TOP_K = 10
_LOG_LABEL = "snsf_rag"


def _split_filters(
    filters: dict[str, Any] | None,
) -> tuple[dict[str, Any], Optional[str], list[str]]:
    """Split filters into (qdrant payload filters, institute substring, dropped)."""
    if not filters:
        return {}, None, []
    qfilters: dict[str, Any] = {}
    institute_substr: Optional[str] = None
    dropped: list[str] = []
    for key, value in filters.items():
        if value is None:
            continue
        if key not in _ALLOWLISTED_FILTERS:
            dropped.append(key)
            continue
        if isinstance(value, dict):
            # Accept {"$eq": "X"} only — the SNSF payload filters are exact-match.
            v = value.get("$eq")
            if not isinstance(v, str) or not v.strip():
                dropped.append(key)
                continue
            value = v.strip()
        if not isinstance(value, str) or not value.strip():
            dropped.append(key)
            continue
        if key == "institute":
            institute_substr = value.strip()
        else:
            qfilters[key] = value.strip()
    if dropped:
        logger.warning(
            "%s: dropped non-allowlisted filter keys (allowed: %s): %s",
            _LOG_LABEL, sorted(_ALLOWLISTED_FILTERS), sorted(dropped),
        )
    return qfilters, institute_substr, dropped


def _to_thin_hit(scope_mode: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Build the LLM-facing hit dict from a QdrantSnsfStore.search result."""
    out: dict[str, Any] = {
        "grant_number": raw.get("grant_number"),
        "title": raw.get("title"),
        "score": raw.get("score"),
        "scope_mode": scope_mode,
        "research_institution": raw.get("research_institution"),
        "main_discipline": raw.get("main_discipline"),
        "start_date": raw.get("start_date"),
        "amount_granted": raw.get("amount_granted"),
        "snippet": make_snippet(raw.get("text")),
    }
    # `institute` is added by the post-filter when applicable.
    if raw.get("institute"):
        out["institute"] = raw["institute"]
    if raw.get("grant_number") is not None:
        out["url"] = f"https://data.snf.ch/grants/grant/{raw['grant_number']}"
    return {k: v for k, v in out.items() if v is not None}


def _post_filter_by_institute(
    candidates: list[dict[str, Any]],
    institute_substring: str,
) -> list[dict[str, Any]]:
    """Resolve the ``institute`` (lab/centre) for candidate grants from DuckDB.

    Same logic as `src.index.snsf.query._post_filter_by_institute` — kept
    separate here so this provider doesn't import from the CLI surface.
    """
    if not candidates:
        return []
    needle = institute_substring.lower()
    grant_ids = [c["grant_number"] for c in candidates if c.get("grant_number") is not None]
    if not grant_ids:
        return []
    from src.index.snsf.storage.duckdb_store import DuckDBStore  # noqa: PLC0415

    store = DuckDBStore.open()
    try:
        placeholders = ",".join(["?"] * len(grant_ids))
        rows = store.connect().execute(
            f"SELECT grant_number, institute FROM grants "  # noqa: S608 — placeholders bound below
            f"WHERE grant_number IN ({placeholders})",
            list(grant_ids),
        ).fetchall()
    finally:
        store.close()

    institute_by_id = {gn: (inst or "") for gn, inst in rows}
    kept: list[dict[str, Any]] = []
    for c in candidates:
        gn = c.get("grant_number")
        inst = institute_by_id.get(gn, "")
        if needle in inst.lower():
            kept.append({**c, "institute": inst})
    return kept


class SnsfRagProvider:
    """Async wrapper around the SNSF Qdrant index."""

    def __init__(
        self,
        *,
        store: QdrantSnsfStore,
        rcp: RcpConfig,
    ) -> None:
        self._store = store
        self._rcp = rcp

    @classmethod
    def from_config(cls, cfg: SnsfIndexConfig) -> SnsfRagProvider:
        return cls(store=QdrantSnsfStore(cfg), rcp=cfg.rcp)

    async def search(  # noqa: PLR0911 — each early return is a graceful-fail check
        self,
        query: str,
        *,
        scope_mode: ScopeMode = "switzerland",
        top_k: int = _DEFAULT_TOP_K,
        filters: dict[str, Any] | None = None,
        rerank: bool = False,
    ) -> list[dict[str, Any]]:
        if not isinstance(query, str) or not query.strip():
            return []
        if scope_mode not in _KNOWN_SCOPE_MODES:
            logger.warning("%s: unknown scope_mode %r", _LOG_LABEL, scope_mode)
            return []

        # Skip if the per-scope collection is missing.
        collection_name = self._store.collection_name(scope_mode)
        if not self._store.client.collection_exists(collection_name):
            logger.info(
                "%s: collection %s missing — returning [] without indexing",
                _LOG_LABEL, collection_name,
            )
            return []

        qfilters, institute_substr, _dropped = _split_filters(filters)

        try:
            vector_np = await embed_query(self._rcp, query)
        except EmbeddingError as exc:
            logger.warning("%s: embed failed — %s", _LOG_LABEL, exc)
            return []
        if not isinstance(vector_np, np.ndarray):
            logger.warning("%s: embed returned non-array %r", _LOG_LABEL, type(vector_np))
            return []
        vector = vector_np.tolist()

        # When --institute is set, most candidates will be filtered out, so
        # widen the ANN net to keep `top_k` non-empty.
        candidate_k = expand_candidate_k(top_k) if rerank else top_k
        if institute_substr:
            candidate_k = max(candidate_k, top_k * 4)

        try:
            raw_hits = await asyncio.to_thread(
                self._store.search,
                scope_mode,
                query_vector=vector,
                top_k=candidate_k,
                **qfilters,  # institution / discipline_l1 / state
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "%s: qdrant search failed (scope=%s) — %s",
                _LOG_LABEL, scope_mode, exc,
            )
            return []

        if institute_substr:
            raw_hits = await asyncio.to_thread(
                _post_filter_by_institute, raw_hits, institute_substr,
            )
            if not raw_hits:
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
            [(h.get("text") or h.get("title") or "") for h in hits],
        )
        try:
            results = await rerank(self._rcp, query, documents, top_n=top_k)
        except RerankError as exc:
            logger.warning("%s: rerank failed — %s", _LOG_LABEL, exc)
            return hits[:top_k]
        adapter = [{"index": r.index, "relevance_score": r.score} for r in results]
        return apply_rerank_indices(hits, adapter, top_k=top_k)


def build_default_provider(
    cfg: SnsfIndexConfig | None = None,
) -> SnsfRagProvider | None:
    if not env_enabled("V2_SNSF_RAG_ENABLED"):
        return None
    try:
        from src.index.snsf.config import load_config  # noqa: PLC0415

        resolved = cfg or load_config()
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s: failed to load config (%s)", _LOG_LABEL, exc)
        return None
    try:
        return SnsfRagProvider.from_config(resolved)
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s: failed to construct provider (%s)", _LOG_LABEL, exc)
        return None


__all__ = ["ScopeMode", "SnsfRagProvider", "build_default_provider"]
