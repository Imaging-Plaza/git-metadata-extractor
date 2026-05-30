"""Adapter wrapping `src.index.dockerhub` for federated search/lookup."""

from __future__ import annotations

import re
from typing import Any

from src.index._federated.registry import EntityRecord, Hit, register

# https://hub.docker.com/r/<ns>/<name>  |  https://hub.docker.com/_/<name>
_RE_HUB_REPO_URL = re.compile(
    r"https?://(?:www\.)?hub\.docker\.com/r/([^/\s?#]+)/([^/\s?#]+)",
    re.I,
)
_RE_HUB_OFFICIAL_URL = re.compile(
    r"https?://(?:www\.)?hub\.docker\.com/_/([^/\s?#]+)",
    re.I,
)


def _normalize(identifier: str) -> str | None:
    """Resolve an identifier to a Docker Hub ``namespace/name`` repo id, or None."""
    s = identifier.strip().rstrip("/")
    m = _RE_HUB_REPO_URL.search(s)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    m = _RE_HUB_OFFICIAL_URL.search(s)
    if m:
        return f"library/{m.group(1)}"
    if s.startswith("http"):
        return None
    for prefix in ("docker.io/", "registry-1.docker.io/"):
        if s.startswith(prefix):
            s = s[len(prefix):]
    s = s.split(":", 1)[0].strip("/")
    if not s:
        return None
    if "/" in s:
        ns, _, name = s.partition("/")
        if ns == "_":
            ns = "library"
        return f"{ns}/{name}" if ns and name else None
    return f"library/{s}"


class DockerhubAdapter:
    name = "dockerhub"
    entity_types = ["image"]

    def search(
        self,
        *,
        query: str,
        entity_type: str | None,  # noqa: ARG002 — single-type index
        top_k: int,
        filters: dict[str, Any] | None,
    ) -> list[Hit]:
        try:
            from src.index.dockerhub.config import load_config
            from src.index.dockerhub.retrieval.semantic import semantic_search
        except Exception:  # noqa: BLE001
            return []
        try:
            cfg = load_config()
            results = semantic_search(
                config=cfg, query=query,
                top_k=top_k, candidate_k=max(top_k * 5, 50),
                filter_payload=filters,
            )
        except Exception:  # noqa: BLE001
            return []
        out: list[Hit] = []
        for r in results:
            payload = r.get("payload") or {}
            repo_id = payload.get("repo_id") or payload.get("entity_id")
            if not repo_id:
                continue
            if r.get("entity") is None:
                continue
            out.append(Hit(
                index=self.name, entity_type="image",
                id=str(repo_id),
                title=str(repo_id),
                score=float(r.get("rerank_score") or r.get("vector_score") or 0.0),
                summary=_summary(payload),
                url=f"https://hub.docker.com/r/{repo_id}",
                payload=payload,
            ))
        return out

    def lookup(self, identifier: str) -> list[EntityRecord]:
        repo_id = _normalize(identifier)
        if not repo_id:
            return []
        try:
            from src.index.dockerhub.storage.duckdb_store import DockerhubStore
        except Exception:  # noqa: BLE001
            return []
        store = DockerhubStore.open()
        if hasattr(store, "fetch_image"):
            row = store.fetch_image(repo_id)
            if row is not None:
                return [EntityRecord(
                    index=self.name, entity_type="image", id=repo_id,
                    data=row, url=f"https://hub.docker.com/r/{repo_id}",
                )]
        return []


def _summary(payload: dict[str, Any]) -> str | None:
    parts = []
    pulls = payload.get("pull_count")
    if isinstance(pulls, int):
        parts.append(f"{pulls:,} pulls")
    if payload.get("is_official"):
        parts.append("official")
    return " — ".join(parts) if parts else None


register(DockerhubAdapter())
