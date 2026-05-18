"""Shared helpers for `POST /v2/indices/<name>/search` glue modules."""

from __future__ import annotations

from typing import Any

from src.v2.api_models import IndexSearchHit


def hit_from_raw(raw: dict[str, Any], *, entity_key: str = "entity") -> IndexSearchHit:
    """Normalise a per-index hit dict into the uniform :class:`IndexSearchHit`.

    Each per-index :func:`semantic_search` returns a list of dicts with keys
    ``id`` / ``vector_score`` / ``rerank_score`` / ``payload`` plus an
    index-specific entity field (most use ``entity``, ORCID uses ``person``).
    """
    return IndexSearchHit(
        id=str(raw.get("id") or ""),
        vector_score=raw.get("vector_score"),
        rerank_score=raw.get("rerank_score"),
        payload=raw.get("payload") or {},
        entity=raw.get(entity_key),
    )


__all__ = ["hit_from_raw"]
