"""Adapter wrapping `src.index.infoscience` for federated search/lookup."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from src.index._federated.registry import EntityRecord, Hit, register

_RE_UUID = re.compile(
    r"\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b",
    re.I,
)
_RE_INFOSCIENCE_URL = re.compile(
    r"https?://infoscience\.epfl\.ch/entities/publication/"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.I,
)

_LINKS_INDEX = Path("data/index/infoscience/dumps/infoscience_links_index.json")


class InfoscienceAdapter:
    name = "infoscience"
    entity_types = ["chunks", "articles", "persons", "organizations"]

    def search(
        self,
        *,
        query: str,
        entity_type: str | None,
        top_k: int,
        filters: dict[str, Any] | None,
    ) -> list[Hit]:
        try:
            from src.index.infoscience.config import load_config
            from src.index.infoscience.pipeline import query as infoscience_query
        except Exception:  # noqa: BLE001
            return []
        cfg = load_config()
        target = entity_type or "chunks"
        try:
            qr = asyncio.run(infoscience_query(
                cfg, query, target=target, where=filters, top_k=top_k,
            ))
        except Exception:  # noqa: BLE001
            return []
        # `query()` returns a QueryResult dataclass; the hits are in `.rows`.
        results = getattr(qr, "rows", None) or []
        out: list[Hit] = []
        for r in results:
            # Infoscience returns dicts with 'id', 'metadata', 'text', 'distance'/'score'.
            md = r.get("metadata") or r.get("payload") or {}
            uuid_ = md.get("article_uuid") or md.get("uuid") or r.get("id")
            if not uuid_:
                continue
            title = md.get("title") or md.get("name") or str(uuid_)
            score = float(r.get("rerank_score") or r.get("score") or
                          (1.0 - r.get("distance", 1.0)))
            out.append(Hit(
                index=self.name, entity_type=target.rstrip("s") or target,
                id=str(uuid_),
                title=str(title) if title else None,
                score=score,
                summary=(r.get("text") or md.get("title") or "")[:200] or None,
                url=md.get("infoscience_url") or
                    f"https://infoscience.epfl.ch/entities/publication/{uuid_}",
                payload=dict(md),
            ))
        return out

    def lookup(self, identifier: str) -> list[EntityRecord]:
        s = identifier.strip()
        m = _RE_INFOSCIENCE_URL.search(s) or _RE_UUID.search(s)
        if not m:
            return []
        uuid_ = m.group(1).lower()
        # Cheapest path: hit the slim links index file we already use for HF cross-links.
        if not _LINKS_INDEX.exists():
            return []
        with _LINKS_INDEX.open(encoding="utf-8") as fh:
            data = json.load(fh)
        for item in data.get("items", []):
            if (item.get("uuid") or "").lower() == uuid_:
                return [EntityRecord(
                    index=self.name, entity_type="article", id=uuid_,
                    data=item,
                    url=item.get("infoscience_url") or
                        f"https://infoscience.epfl.ch/entities/publication/{uuid_}",
                )]
        return []


register(InfoscienceAdapter())
