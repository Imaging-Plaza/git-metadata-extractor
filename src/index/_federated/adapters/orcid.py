"""Adapter wrapping `src.index.orcid` for federated search/lookup."""

from __future__ import annotations

import re
from typing import Any

from src.index._federated.registry import EntityRecord, Hit, register

_RE_ORCID = re.compile(r"\b(\d{4}-\d{4}-\d{4}-\d{3}[\dX])\b")
_RE_ORCID_URL = re.compile(r"https?://orcid\.org/(\d{4}-\d{4}-\d{4}-\d{3}[\dX])", re.I)


class OrcidAdapter:
    name = "orcid"
    entity_types = ["persons", "employments", "educations"]

    def search(
        self,
        *,
        query: str,
        entity_type: str | None,
        top_k: int,
        filters: dict[str, Any] | None,
    ) -> list[Hit]:
        from src.index.orcid.config import load_config
        from src.index.orcid.retrieval.semantic import semantic_search

        config = load_config()
        types_to_query = [entity_type] if entity_type else ["persons"]
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
                oid = payload.get("orcid_id") or payload.get("orcid")
                if not oid:
                    continue
                singular = payload.get("entity_type") or et.rstrip("s") or et
                full_name = (
                    payload.get("display_name") or payload.get("name")
                    or f"{payload.get('given_names', '')} {payload.get('family_name', '')}".strip()
                    or oid
                )
                out.append(Hit(
                    index=self.name,
                    entity_type=singular,
                    id=oid,
                    title=str(full_name) if full_name else None,
                    score=float(r.get("rerank_score") or r.get("vector_score") or 0.0),
                    summary=_summary(payload),
                    url=f"https://orcid.org/{oid}",
                    payload=payload,
                ))
        return out

    def lookup(self, identifier: str) -> list[EntityRecord]:
        s = identifier.strip()
        m = _RE_ORCID_URL.search(s) or _RE_ORCID.search(s)
        if not m:
            return []
        oid = m.group(1)
        try:
            from src.index.orcid.storage.duckdb_store import DuckDBStore
        except Exception:  # noqa: BLE001
            return []
        store = DuckDBStore.open()
        if hasattr(store, "fetch_person"):
            row = store.fetch_person(oid)
            if row is not None:
                return [EntityRecord(
                    index=self.name, entity_type="person", id=oid,
                    data=row, url=f"https://orcid.org/{oid}",
                )]
        return []


def _summary(payload: dict[str, Any]) -> str | None:
    parts = [
        str(payload.get("display_name") or payload.get("name") or ""),
        str(payload.get("organization") or ""),
        str(payload.get("role") or ""),
    ]
    parts = [p for p in parts if p]
    return " — ".join(parts) if parts else None


register(OrcidAdapter())
