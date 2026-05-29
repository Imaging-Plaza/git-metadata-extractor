"""Adapter wrapping `src.index.github_organizations` for federated search/lookup."""

from __future__ import annotations

import re
from typing import Any

from src.index._federated.registry import EntityRecord, Hit, register

_RE_GH_ORG_URL = re.compile(
    r"https?://(?:www\.)?github\.com/(?:orgs/)?([^/\s?#]+)/?$",
    re.I,
)


class GitHubOrganizationsAdapter:
    name = "github_organizations"
    entity_types = ["organization"]

    def search(
        self,
        *,
        query: str,
        entity_type: str | None,  # noqa: ARG002 — single-type index
        top_k: int,
        filters: dict[str, Any] | None,
    ) -> list[Hit]:
        try:
            from src.index.github_organizations.config import load_config
            from src.index.github_organizations.retrieval.semantic import semantic_search
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
            login = payload.get("login")
            if not login:
                continue
            out.append(Hit(
                index=self.name, entity_type="organization",
                id=str(login),
                title=payload.get("name") or str(login),
                score=float(r.get("rerank_score") or r.get("vector_score") or 0.0),
                summary=_org_summary(payload),
                url=payload.get("html_url") or f"https://github.com/{login}",
                payload=payload,
            ))
        return out

    def lookup(self, identifier: str) -> list[EntityRecord]:
        """Lookup by bare org handle, plain `github.com/<org>` URL, or
        `github.com/orgs/<org>` URL (the web-UI form)."""
        s = identifier.strip().rstrip("/")
        login: str | None = None
        m = _RE_GH_ORG_URL.search(s)
        if m:
            login = m.group(1)
        elif s and "/" not in s and not s.startswith("http"):
            login = s
        if not login:
            return []
        try:
            from src.index.github_organizations.storage.duckdb_store import (
                GitHubOrganizationsStore,
            )
        except Exception:  # noqa: BLE001
            return []
        store = GitHubOrganizationsStore.open()
        if hasattr(store, "fetch_organization"):
            row = store.fetch_organization(login)
            if row is not None:
                return [EntityRecord(
                    index=self.name, entity_type="organization", id=login,
                    data=row,
                    url=row.get("html_url") or f"https://github.com/{login}",
                )]
        return []


def _org_summary(payload: dict[str, Any]) -> str | None:
    parts = [
        str(payload.get("location") or ""),
    ]
    if payload.get("is_verified"):
        parts.append("verified")
    parts = [p for p in parts if p]
    return " — ".join(parts) if parts else None


register(GitHubOrganizationsAdapter())
