"""Federated search orchestrator: fan out to adapters in parallel, merge by score."""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from src.index._federated.registry import REGISTRY, Hit, IndexAdapter, load_adapters

LOGGER = logging.getLogger(__name__)


def federated_search(
    query: str,
    *,
    indices: list[str] | None = None,
    entity_type: str | None = None,
    top_k_per_index: int = 5,
    top_k_overall: int = 20,
    filters: dict[str, Any] | None = None,
    rerank: bool = False,
) -> dict[str, Any]:
    """Run `query` against every registered (or named) index in parallel.

    Returns `{hits: [Hit-dicts...], by_index: {index: count}, errors: {...}}`.
    Hits are sorted by score descending; per-index slot allows top_k_per_index
    contributions; overall trimmed to top_k_overall.

    When `rerank=True` the merged candidate pool is sent through the RCP
    cross-encoder once for a globally-fair ordering — the per-adapter scores
    aren't directly comparable (each adapter ranks within its own pool).
    """
    adapters = load_adapters(only=indices) if indices is not None else load_adapters()
    if not adapters:
        return {"hits": [], "by_index": {}, "errors": {"_": "no adapters loaded"}}

    hits: list[Hit] = []
    errors: dict[str, str] = {}
    by_index: dict[str, int] = {}

    def _run(adapter: IndexAdapter) -> tuple[str, list[Hit] | str]:
        try:
            return (adapter.name, adapter.search(
                query=query,
                entity_type=entity_type,
                top_k=top_k_per_index,
                filters=filters,
            ))
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("federated: adapter %s search failed: %s", adapter.name, exc)
            return (adapter.name, f"{type(exc).__name__}: {exc}")

    with ThreadPoolExecutor(max_workers=max(len(adapters), 1)) as ex:
        futures = [ex.submit(_run, a) for a in adapters]
        for fut in as_completed(futures):
            name, result = fut.result()
            if isinstance(result, str):
                errors[name] = result
                continue
            by_index[name] = len(result)
            hits.extend(result)

    if rerank and hits:
        hits, rerank_err = _cross_index_rerank(query, hits)
        if rerank_err:
            errors["_rerank"] = rerank_err

    hits.sort(key=lambda h: -h.score)
    trimmed = hits[:top_k_overall]
    return {
        "hits": [_hit_to_dict(h) for h in trimmed],
        "by_index": by_index,
        "errors": errors,
        "registered_indices": sorted(REGISTRY.keys()),
        "reranked": bool(rerank and not errors.get("_rerank")),
    }


def _cross_index_rerank(query: str, hits: list[Hit]) -> tuple[list[Hit], str | None]:
    """Send the merged candidate pool through the RCP cross-encoder.

    Each `Hit.score` is replaced with the cross-encoder relevance score so
    the subsequent sort produces a globally-fair ordering across indices.

    The reranker config is borrowed from whichever per-index module is
    available — they all use the same RCP endpoint and model. HF first.
    """
    documents = [_doc_for_rerank(h) for h in hits]
    try:
        rerank_client = _build_reranker()
    except Exception as exc:  # noqa: BLE001
        return hits, f"reranker config: {type(exc).__name__}: {exc}"
    if rerank_client is None:
        return hits, "no per-index module available to source RCP config"
    try:
        scored = asyncio.run(rerank_client.rerank(query, documents, top_n=len(documents)))
    except Exception as exc:  # noqa: BLE001
        return hits, f"rerank call: {type(exc).__name__}: {exc}"
    if not scored:
        return hits, "rerank returned empty"
    # `scored` is `[{"index": int, "relevance_score": float}, ...]` ordered by score.
    out: list[Hit] = []
    for s in scored:
        idx = s.get("index")
        if not isinstance(idx, int) or not (0 <= idx < len(hits)):
            continue
        out.append(_clone_hit(hits[idx], score=float(s.get("relevance_score") or 0.0)))
    return out, None


def _doc_for_rerank(h: Hit) -> str:
    """Build the rerank document string for one Hit."""
    parts = [h.title or "", h.summary or "", f"[{h.index}/{h.entity_type}]", h.id or ""]
    parts = [p for p in parts if p]
    return " — ".join(parts) if parts else "(empty)"


def _clone_hit(h: Hit, *, score: float) -> Hit:
    return Hit(
        index=h.index, entity_type=h.entity_type, id=h.id,
        title=h.title, score=score, summary=h.summary, url=h.url,
        payload=h.payload,
    )


def _build_reranker() -> Any | None:  # noqa: ANN401 — duck-typed RCP reranker
    """Try each known per-index module's reranker until one loads."""
    candidates = (
        ("src.index.huggingface.config", "load_config",
         "src.index.huggingface.rerank.rcp_client", "RCPRerankerClient"),
        ("src.index.openalex.config", "load_config",
         "src.index.openalex.rerank.rcp_client", "RCPRerankerClient"),
        ("src.index.zenodo_records.config", "load_config",
         "src.index.zenodo_records.rerank.rcp_client", "RCPRerankerClient"),
        ("src.index.orcid.config", "load_config",
         "src.index.orcid.rerank.rcp_client", "RCPRerankerClient"),
    )
    from importlib import import_module
    for cfg_mod, cfg_attr, rerank_mod, rerank_class in candidates:
        try:
            cfg = getattr(import_module(cfg_mod), cfg_attr)()
            klass = getattr(import_module(rerank_mod), rerank_class)
            return klass(cfg)
        except Exception:  # noqa: BLE001
            continue
    return None


def _hit_to_dict(h: Hit) -> dict[str, Any]:
    return {
        "index": h.index,
        "entity_type": h.entity_type,
        "id": h.id,
        "title": h.title,
        "score": h.score,
        "summary": h.summary,
        "url": h.url,
        "payload": h.payload,
    }
