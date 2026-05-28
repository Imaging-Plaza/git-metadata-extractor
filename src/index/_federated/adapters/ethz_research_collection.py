"""Adapter wrapping `src.index.ethz_research_collection` for federated search/lookup.

Mirrors the `infoscience` adapter — both are DSpace-based with the same
`QueryResult` shape from `pipeline.query()`.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from src.index._federated.registry import EntityRecord, Hit, register

_RE_UUID = re.compile(
    r"\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b",
    re.I,
)
_RE_RC_URL = re.compile(
    r"https?://(?:www\.)?research-collection\.ethz\.ch/handle/(\S+)",
    re.I,
)


class EthzResearchCollectionAdapter:
    name = "ethz_research_collection"
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
            from src.index.ethz_research_collection.config import load_config
            from src.index.ethz_research_collection.pipeline import (
                query as rc_query,
            )
        except Exception:  # noqa: BLE001
            return []
        cfg = load_config()
        target = entity_type or "chunks"
        try:
            qr = asyncio.run(rc_query(
                cfg, query, target=target, where=filters, top_k=top_k,
            ))
        except Exception:  # noqa: BLE001
            return []
        results = getattr(qr, "rows", None) or []
        out: list[Hit] = []
        for r in results:
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
                url=md.get("source_url") or md.get("research_collection_url"),
                payload=dict(md),
            ))
        return out

    def lookup(self, identifier: str) -> list[EntityRecord]:
        s = identifier.strip()
        m = _RE_RC_URL.search(s) or _RE_UUID.search(s)
        if not m:
            return []
        # Without a stable local lookup index, return a thin record acknowledging
        # the identifier shape; richer hydration can come from search if needed.
        identifier_value = m.group(1)
        return [EntityRecord(
            index=self.name, entity_type="article", id=identifier_value,
            data={"id": identifier_value, "source": "research-collection.ethz.ch"},
            url=(s if s.startswith("http") else
                 f"https://www.research-collection.ethz.ch/handle/{identifier_value}"),
        )]


register(EthzResearchCollectionAdapter())
