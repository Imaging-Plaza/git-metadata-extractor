"""Async RAG provider over the federated cross-index layer.

Wraps :mod:`open_pulse_sources.index._federated` so the v2 LLM agents can call into one
tool that searches all six RAG indices in parallel.

Two methods:

* ``search(query, *, indices, entity_type, top_k, top_k_per_index, filters)``
  — fan-out semantic search; returns merged hits sorted by score.
* ``lookup(identifier, *, indices)`` — cross-index identifier resolution;
  returns matched canonical records.

Graceful degradation: per-adapter exceptions are absorbed by the federated
layer; this provider further wraps the call in a broad except so a missing
adapter, RCP outage, or Qdrant downtime can't bubble into the agent loop.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

_LOG_LABEL = "federated.rag"
_DEFAULT_TOP_K = 10
_DEFAULT_TOP_K_PER_INDEX = 3


class FederatedRagProvider:
    """Async wrapper around `open_pulse_sources.index._federated.search` / `entity`.

    Stateless — no Qdrant / RCP handles to manage. Each call lazily loads
    the registered adapters (the federated module already caches imports
    on first call, so subsequent invocations are fast).
    """

    async def search(
        self,
        query: str,
        *,
        indices: list[str] | None = None,
        entity_type: str | None = None,
        top_k: int = _DEFAULT_TOP_K,
        top_k_per_index: int = _DEFAULT_TOP_K_PER_INDEX,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(query, str) or not query.strip():
            return {"hits": [], "by_index": {}, "errors": {}}

        from open_pulse_sources.index._federated.search import federated_search

        try:
            return await asyncio.to_thread(
                federated_search,
                query,
                indices=indices,
                entity_type=entity_type,
                top_k_per_index=top_k_per_index,
                top_k_overall=top_k,
                filters=filters,
            )
        except Exception as exc:  # noqa: BLE001 — must not bubble into the agent
            logger.warning("%s: search failed — %s", _LOG_LABEL, exc)
            return {"hits": [], "by_index": {}, "errors": {"_": str(exc)}}

    async def lookup(
        self,
        identifier: str,
        *,
        indices: list[str] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(identifier, str) or not identifier.strip():
            return {"identifier": identifier, "records": [], "by_index": {}, "errors": {}}

        from open_pulse_sources.index._federated.entity import cross_index_lookup

        try:
            return await asyncio.to_thread(
                cross_index_lookup,
                identifier,
                indices=indices,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s: lookup failed — %s", _LOG_LABEL, exc)
            return {
                "identifier": identifier, "records": [], "by_index": {},
                "errors": {"_": str(exc)},
            }


def build_default_provider() -> FederatedRagProvider:
    """Convenience for `dependencies.py` wiring — no config needed."""
    return FederatedRagProvider()


__all__ = ["FederatedRagProvider", "build_default_provider"]
