"""Adapter wrapping `src.index.zenodo_records` for federated search/lookup."""

from __future__ import annotations

import re
from typing import Any

from src.index._federated.registry import EntityRecord, Hit, register

_RE_DIGITS_ONLY = re.compile(r"^\d{4,10}$")
_RE_ZENODO_URL = re.compile(
    r"https?://(?:www\.|sandbox\.)?zenodo\.org/(?:records?|deposit)/(\d+)",
    re.I,
)
_RE_ZENODO_DOI = re.compile(r"10\.5281/zenodo\.(\d+)", re.I)


class ZenodoRecordsAdapter:
    name = "zenodo_records"
    entity_types = ["zenodo_records"]

    def search(
        self,
        *,
        query: str,
        entity_type: str | None,  # noqa: ARG002 — single-type index
        top_k: int,
        filters: dict[str, Any] | None,
    ) -> list[Hit]:
        from src.index.zenodo_records.config import load_config
        from src.index.zenodo_records.retrieval.semantic import semantic_search

        config = load_config()
        try:
            results = semantic_search(
                config=config, query=query,
                top_k=top_k, candidate_k=max(top_k * 5, 50),
                filter_payload=filters,
            )
        except Exception:  # noqa: BLE001
            return []
        out: list[Hit] = []
        for r in results:
            payload = r.get("payload") or {}
            zid = payload.get("zenodo_id") or payload.get("id")
            if not zid:
                continue
            title = payload.get("title") or str(zid)
            out.append(Hit(
                index=self.name, entity_type="zenodo_record",
                id=str(zid),
                title=str(title) if title else None,
                score=float(r.get("rerank_score") or r.get("vector_score") or 0.0),
                summary=_summary(payload),
                url=f"https://zenodo.org/records/{zid}",
                payload=payload,
            ))
        return out

    def lookup(self, identifier: str) -> list[EntityRecord]:
        s = identifier.strip()
        m = _RE_ZENODO_URL.search(s) or _RE_ZENODO_DOI.search(s)
        if m:
            zid = m.group(1)
        elif _RE_DIGITS_ONLY.match(s):
            zid = s
        else:
            return []
        try:
            from src.index.zenodo_records.storage.duckdb_store import ZenodoRecordsStore
        except Exception:  # noqa: BLE001
            return []
        store = ZenodoRecordsStore.open()
        try:
            row = store.fetch_record(zid)
            if row is None:
                # Citation may reference the concept_recid (parent) — fall
                # back to the most-recent version under that concept.
                row = store.fetch_record_by_concept(zid)
            if row is None:
                return []
            canonical_id = str(row.get("zenodo_id") or zid)
            return [EntityRecord(
                index=self.name, entity_type="zenodo_record", id=canonical_id,
                data=row, url=f"https://zenodo.org/records/{canonical_id}",
            )]
        finally:
            store.close()


def _summary(payload: dict[str, Any]) -> str | None:
    parts = [
        str(payload.get("title") or ""),
        str(payload.get("year") or ""),
        str(payload.get("resource_type") or ""),
        str(payload.get("doi") or ""),
    ]
    parts = [p for p in parts if p]
    return " — ".join(parts) if parts else None


register(ZenodoRecordsAdapter())
