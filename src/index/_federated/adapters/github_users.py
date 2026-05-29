"""Adapter wrapping `src.index.github_users` for federated search/lookup."""

from __future__ import annotations

import re
from typing import Any

from src.index._federated.registry import EntityRecord, Hit, register

_RE_GH_USER_URL = re.compile(
    r"https?://(?:www\.)?github\.com/([^/\s?#]+)/?$",
    re.I,
)


class GitHubUsersAdapter:
    name = "github_users"
    entity_types = ["user"]

    def search(
        self,
        *,
        query: str,
        entity_type: str | None,  # noqa: ARG002 — single-type index
        top_k: int,
        filters: dict[str, Any] | None,
    ) -> list[Hit]:
        try:
            from src.index.github_users.config import load_config
            from src.index.github_users.retrieval.semantic import semantic_search
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
                index=self.name, entity_type="user",
                id=str(login),
                title=payload.get("name") or str(login),
                score=float(r.get("rerank_score") or r.get("vector_score") or 0.0),
                summary=_user_summary(payload),
                url=payload.get("html_url") or f"https://github.com/{login}",
                payload=payload,
            ))
        return out

    def lookup(self, identifier: str) -> list[EntityRecord]:
        """Lookup by bare login or full GitHub profile URL."""
        s = identifier.strip().rstrip("/")
        login: str | None = None
        m = _RE_GH_USER_URL.search(s)
        if m:
            login = m.group(1)
        elif s and "/" not in s and not s.startswith("http"):
            login = s
        if not login:
            return []
        try:
            from src.index.github_users.storage.duckdb_store import GitHubUsersStore
        except Exception:  # noqa: BLE001
            return []
        store = GitHubUsersStore.open()
        if hasattr(store, "fetch_user"):
            row = store.fetch_user(login)
            if row is not None:
                return [EntityRecord(
                    index=self.name, entity_type="user", id=login,
                    data=row,
                    url=row.get("html_url") or f"https://github.com/{login}",
                )]
        return []


def _user_summary(payload: dict[str, Any]) -> str | None:
    parts = [
        str(payload.get("company") or ""),
        str(payload.get("location") or ""),
    ]
    parts = [p for p in parts if p]
    return " — ".join(parts) if parts else None


register(GitHubUsersAdapter())
