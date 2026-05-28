"""Federated adapter for the EPFL Graph disciplines index.

Exposes a single ``disciplines`` entity type. ``search`` runs the
semantic-search pipeline; ``lookup`` resolves a slug-style category id
(``neuroscience``, ``topics-in-natural-language-processing``) directly
out of DuckDB without any RAG round-trip.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from src.index._federated.registry import EntityRecord, Hit, register

_RE_GRAPHSEARCH_URL = re.compile(
    r"https?://graphsearch\.epfl\.ch/(?:[a-z]{2}/)?category/([a-z0-9][a-z0-9-]+)",
    re.IGNORECASE,
)
_RE_SLUG = re.compile(r"^[a-z][a-z0-9-]{2,}$")


class EpflGraphAdapter:
    name = "epfl_graph"
    entity_types: ClassVar[list[str]] = ["disciplines"]

    def search(
        self,
        *,
        query: str,
        entity_type: str | None,
        top_k: int,
        filters: dict[str, Any] | None,
    ) -> list[Hit]:
        if entity_type and entity_type not in {"disciplines", "discipline"}:
            return []
        try:
            from src.index.epfl_graph.config import load_config  # noqa: PLC0415
            from src.index.epfl_graph.retrieval.semantic import (  # noqa: PLC0415
                semantic_search,
            )
        except Exception:  # noqa: BLE001
            return []
        try:
            config = load_config()
            results = semantic_search(
                config=config,
                query=query,
                top_k=top_k,
                candidate_k=max(top_k * 5, 50),
                min_depth=(
                    int(filters.get("depth", 0))
                    if isinstance(filters, dict) and "depth" in filters
                    else None
                ),
            )
        except Exception:  # noqa: BLE001
            return []
        out: list[Hit] = []
        for r in results:
            category_id = r.get("category_id")
            if not category_id:
                continue
            payload = r.get("payload") or {}
            score = r.get("rerank_score") or r.get("vector_score") or 0.0
            out.append(
                Hit(
                    index=self.name,
                    entity_type="discipline",
                    id=str(category_id),
                    title=r.get("name") or str(category_id),
                    score=float(score),
                    summary=_summary(payload),
                    url=r.get("graphsearch_url"),
                    payload={
                        "category_id": category_id,
                        "name": r.get("name"),
                        "depth": r.get("depth"),
                        "wikipedia_url": r.get("wikipedia_url"),
                        "graphsearch_url": r.get("graphsearch_url"),
                        "chain": r.get("chain"),
                    },
                ),
            )
        return out

    def lookup(self, identifier: str) -> list[EntityRecord]:
        slug = _extract_slug(identifier)
        if not slug:
            return []
        try:
            from src.index.epfl_graph.config import load_config  # noqa: PLC0415
            from src.index.epfl_graph.storage.duckdb_store import (  # noqa: PLC0415
                EpflGraphStore,
            )
        except Exception:  # noqa: BLE001
            return []
        try:
            config = load_config()
            store = EpflGraphStore.open(config.paths.duckdb_path)
        except Exception:  # noqa: BLE001
            return []
        try:
            record = store.fetch_category(slug)
        finally:
            store.close()
        if record is None:
            return []
        return [
            EntityRecord(
                index=self.name,
                entity_type="discipline",
                id=slug,
                data=record,
                url=record.get("graphsearch_url"),
            ),
        ]


def _extract_slug(identifier: str) -> str | None:
    s = (identifier or "").strip()
    if not s:
        return None
    match = _RE_GRAPHSEARCH_URL.search(s)
    if match:
        return match.group(1).lower()
    if _RE_SLUG.match(s):
        return s.lower()
    return None


def _summary(payload: dict[str, Any]) -> str | None:
    parts: list[str] = []
    name = payload.get("name")
    if name:
        parts.append(str(name))
    depth = payload.get("depth")
    if isinstance(depth, int):
        parts.append(f"depth={depth}")
    n_concepts = payload.get("n_concepts")
    if isinstance(n_concepts, int) and n_concepts:
        parts.append(f"{n_concepts} anchor concepts")
    return " — ".join(parts) if parts else None


register(EpflGraphAdapter())
