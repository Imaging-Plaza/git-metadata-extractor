"""Adapter wrapping `src.index.ror` for federated search/lookup."""

from __future__ import annotations

import re
from typing import Any

from src.index._federated.registry import EntityRecord, Hit, register

_RE_ROR_URL = re.compile(r"https?://ror\.org/([0-9a-z]{9})", re.I)
_RE_ROR_ID = re.compile(r"\b([0-9a-z]{9})\b")


class RorAdapter:
    name = "ror"
    entity_types = ["organizations"]

    def search(
        self,
        *,
        query: str,
        entity_type: str | None,  # noqa: ARG002
        top_k: int,
        filters: dict[str, Any] | None,
    ) -> list[Hit]:
        from src.index.ror.config import load_config
        from src.index.ror.query import query_rag_sync

        cfg = load_config()
        country = (filters or {}).get("country_code") or (filters or {}).get("country")
        try:
            scored = query_rag_sync(
                cfg, query, top_k=top_k, country=country,
            )
        except Exception:  # noqa: BLE001
            return []
        out: list[Hit] = []
        for s in scored:
            # ScoredRecord has .record (dict-like) and .score / .rerank_score
            rec = getattr(s, "record", None) or {}
            ror_id = rec.get("id") or rec.get("ror_id") or rec.get("ror")
            if not ror_id:
                continue
            out.append(Hit(
                index=self.name, entity_type="organization",
                id=str(ror_id),
                title=rec.get("name") or rec.get("display_name"),
                score=float(getattr(s, "rerank_score", None) or getattr(s, "score", 0.0)),
                summary=_summary(rec),
                url=str(ror_id) if str(ror_id).startswith("http") else f"https://ror.org/{ror_id}",
                payload=dict(rec),
            ))
        return out

    def lookup(self, identifier: str) -> list[EntityRecord]:
        s = identifier.strip()
        m = _RE_ROR_URL.search(s)
        ror_id = m.group(1) if m else None
        if not ror_id:
            m2 = _RE_ROR_ID.fullmatch(s)
            if m2:
                ror_id = m2.group(1)
        if not ror_id:
            return []
        try:
            from src.index.ror.config import load_config
            from src.index.ror.query import lookup_dump
        except Exception:  # noqa: BLE001
            return []
        cfg = load_config()
        try:
            matches = lookup_dump(cfg, f"https://ror.org/{ror_id}")
        except Exception:  # noqa: BLE001
            return []
        records: list[EntityRecord] = []
        for dm in matches:
            rec = getattr(dm, "record", None) or {}
            records.append(EntityRecord(
                index=self.name, entity_type="organization", id=ror_id,
                data=dict(rec), url=f"https://ror.org/{ror_id}",
            ))
        return records


def _summary(rec: dict[str, Any]) -> str | None:
    parts = [
        str(rec.get("name") or rec.get("display_name") or ""),
        str((rec.get("country") or {}).get("country_code") if isinstance(rec.get("country"), dict) else (rec.get("country") or "")),
        str(rec.get("types", [None])[0] if rec.get("types") else ""),
    ]
    parts = [p for p in parts if p]
    return " — ".join(parts) if parts else None


register(RorAdapter())
