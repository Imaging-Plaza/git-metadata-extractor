"""Adapter wrapping `src.index.openalex` for federated search/lookup."""

from __future__ import annotations

import re
from typing import Any

from src.index._federated.registry import EntityRecord, Hit, register

_RE_OPENALEX_ID = re.compile(r"\b([WAISCT]\d{6,12})\b")
_RE_OPENALEX_URL = re.compile(r"https?://openalex\.org/([WAISCT]\d{6,12})", re.I)
_RE_DOI = re.compile(r"\b10\.\d{4,9}/[^\s]+\b")


class OpenAlexAdapter:
    name = "openalex"
    entity_types = ["works", "authors", "institutions", "sources", "topics", "concepts"]

    def search(
        self,
        *,
        query: str,
        entity_type: str | None,
        top_k: int,
        filters: dict[str, Any] | None,
    ) -> list[Hit]:
        from src.index.openalex.config import load_config
        from src.index.openalex.retrieval.semantic import semantic_search

        config = load_config()
        types_to_query = [entity_type] if entity_type else ["works"]
        out: list[Hit] = []
        for et in types_to_query:
            try:
                results = semantic_search(
                    config=config, query=query, entity_type=et,
                    top_k=top_k, candidate_k=max(top_k * 5, 50),
                    filter_payload=filters,
                )
            except Exception:  # noqa: BLE001
                continue
            for r in results:
                payload = r.get("payload") or {}
                openalex_id = payload.get("openalex_id") or payload.get("id")
                if not openalex_id:
                    continue
                singular = (payload.get("entity_type") or et.rstrip("s") or et)
                title = payload.get("title") or payload.get("display_name") or openalex_id
                out.append(Hit(
                    index=self.name,
                    entity_type=singular,
                    id=str(openalex_id),
                    title=str(title) if title else None,
                    score=float(r.get("rerank_score") or r.get("vector_score") or 0.0),
                    summary=_summary(payload),
                    url=_canonical_url(openalex_id),
                    payload=payload,
                ))
        return out

    def lookup(self, identifier: str) -> list[EntityRecord]:
        s = identifier.strip()
        # Try OpenAlex URL → bare ID first.
        m = _RE_OPENALEX_URL.search(s) or _RE_OPENALEX_ID.search(s)
        if not m:
            return []
        oid = m.group(1)
        try:
            from src.index.openalex.storage.duckdb_store import OpenAlexStore
        except Exception:  # noqa: BLE001
            return []
        store = OpenAlexStore.open()
        # Only `fetch_work` is exposed today; hydrate that for W-IDs.
        if oid.startswith("W") and hasattr(store, "fetch_work"):
            row = store.fetch_work(oid)
            if row is not None:
                return [EntityRecord(
                    index=self.name, entity_type="work", id=oid,
                    data=row, url=_canonical_url(oid),
                )]
        return []


def _summary(payload: dict[str, Any]) -> str | None:
    parts = [
        str(payload.get("title") or payload.get("display_name") or ""),
        str(payload.get("publication_year") or payload.get("year") or ""),
        str(payload.get("doi") or ""),
    ]
    parts = [p for p in parts if p]
    return " — ".join(parts) if parts else None


def _canonical_url(openalex_id: str) -> str | None:
    if not openalex_id:
        return None
    return f"https://openalex.org/{openalex_id}"


register(OpenAlexAdapter())
