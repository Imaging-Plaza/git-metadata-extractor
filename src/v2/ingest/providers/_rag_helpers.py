"""Shared helpers for v2 RAG providers backed by the index/* Qdrant indices.

Per-index providers (HuggingFace / OpenAlex / Zenodo / ORCID / ROR) each
have their own ``QdrantStore`` and embedder/reranker classes with subtly
different signatures. Rather than force them under a common base, this
module provides the small set of cross-cutting utilities every provider
needs:

* ``filter_allowlist`` — drop non-allowlisted keys with a warning.
* ``to_simple_filter_payload`` — translate the LLM-facing operator dict
  (``{"$gte": X}``, ``{"$in": [...]}``, ...) into the simpler dict shape
  the HF/OpenAlex/ORCID/Zenodo stores accept (``{"gte": X}``, ``[..]``).
* ``expand_candidate_k`` — widen the vector top-k when rerank is on so
  the cross-encoder has room to reorder.
* ``apply_rerank_indices`` — apply a list of ``(index, score)`` rerank
  hits onto the original Qdrant hit list.
* ``make_snippet`` — truncate a body field to a fixed character budget.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_SNIPPET_CHARS = 320
RERANK_CANDIDATE_MULTIPLIER = 5
RERANK_CANDIDATE_FLOOR = 30


def filter_allowlist(
    payload: dict[str, Any] | None,
    allowed: frozenset[str],
    *,
    log_label: str,
) -> dict[str, Any] | None:
    """Drop keys not in ``allowed`` from ``payload`` and warn once.

    Returns ``None`` when the result is empty so callers can short-circuit.
    """
    if not payload:
        return None
    cleaned: dict[str, Any] = {}
    dropped: list[str] = []
    for key, value in payload.items():
        if key in allowed:
            cleaned[key] = value
        else:
            dropped.append(key)
    if dropped:
        logger.warning(
            "%s: dropped non-allowlisted filter keys: %s",
            log_label, sorted(dropped),
        )
    return cleaned or None


def to_simple_filter_payload(
    payload: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Convert the LLM-facing operator-dict format to the HF/OpenAlex shape.

    Accepts the same operator dict the infoscience tool uses
    (``{"$gte": X, "$lte": Y}``, ``{"$in": [...]}``, ``{"$eq": v}``,
    scalars, lists) and returns a dict where each value is one of:

    * ``{"gte": X, "lte": Y}`` (range)
    * ``[v1, v2, ...]`` (any-of)
    * scalar (eq)

    Operators ``$ne`` and ``$contains`` are dropped with a warning since
    the HF/OpenAlex stores don't natively support them.
    """
    if not payload:
        return None
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, dict):
            range_kwargs: dict[str, Any] = {}
            if "$gte" in value:
                range_kwargs["gte"] = value["$gte"]
            if "$lte" in value:
                range_kwargs["lte"] = value["$lte"]
            if range_kwargs:
                out[key] = range_kwargs
                continue
            if "$in" in value:
                out[key] = list(value["$in"])
                continue
            if "$eq" in value:
                out[key] = value["$eq"]
                continue
            logger.warning(
                "rag filter: unsupported operator dict for %r: %r — skipped",
                key, value,
            )
            continue
        if isinstance(value, list):
            out[key] = list(value)
            continue
        out[key] = value
    return out or None


def expand_candidate_k(
    top_k: int,
    *,
    multiplier: int = RERANK_CANDIDATE_MULTIPLIER,
    floor: int = RERANK_CANDIDATE_FLOOR,
) -> int:
    """Return the candidate top-k to fetch from Qdrant before reranking."""
    return max(top_k * multiplier, floor)


def safe_rerank_documents(
    documents: list[str],
    *,
    placeholder: str = " ",
) -> list[str]:
    """Replace blank documents with a single-space placeholder.

    Some RCP reranker backends 400 when ``documents`` contains empty
    strings ("The decoder prompt cannot be empty"). Substituting a
    non-empty placeholder keeps the index-to-hit mapping stable while
    letting the rerank request go through; placeholder docs simply
    receive low relevance scores.
    """
    return [doc if isinstance(doc, str) and doc else placeholder for doc in documents]


def apply_rerank_indices(
    hits: list[dict[str, Any]],
    rerank_results: list[dict[str, Any]],
    *,
    top_k: int,
) -> list[dict[str, Any]]:
    """Reorder ``hits`` per a rerank result list of ``{index, relevance_score}``.

    Falls through to ``hits[:top_k]`` if the rerank result is empty.
    """
    if not rerank_results:
        return hits[:top_k]
    ordered: list[dict[str, Any]] = []
    seen: set[int] = set()
    for r in rerank_results:
        idx = int(r.get("index", -1))
        score = r.get("relevance_score") or r.get("score")
        if 0 <= idx < len(hits) and idx not in seen and score is not None:
            seen.add(idx)
            rehit = dict(hits[idx])
            rehit["score"] = float(score)
            ordered.append(rehit)
    return ordered[:top_k]


def make_snippet(text: Any, *, max_chars: int = DEFAULT_SNIPPET_CHARS) -> str | None:
    """Trim an arbitrary text field to at most ``max_chars`` chars."""
    if not isinstance(text, str) or not text:
        return None
    text = text.strip()
    if not text:
        return None
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def thin_payload(
    payload: dict[str, Any],
    keys: tuple[str, ...],
    *,
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a thin LLM-facing hit dict from a payload + extra computed fields."""
    out = {key: payload.get(key) for key in keys if payload.get(key) is not None}
    if extras:
        out.update({k: v for k, v in extras.items() if v is not None})
    return out


def truthy_env(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "t", "yes", "y", "on"}


def env_enabled(name: str, *, default: bool = True) -> bool:
    """Resolve a `V2_<INDEX>_RAG_ENABLED`-style toggle with a default."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return truthy_env(raw)


__all__ = [
    "DEFAULT_SNIPPET_CHARS",
    "RERANK_CANDIDATE_FLOOR",
    "RERANK_CANDIDATE_MULTIPLIER",
    "apply_rerank_indices",
    "env_enabled",
    "expand_candidate_k",
    "filter_allowlist",
    "make_snippet",
    "safe_rerank_documents",
    "thin_payload",
    "to_simple_filter_payload",
    "truthy_env",
]
