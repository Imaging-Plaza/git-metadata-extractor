"""Adapter wrapping `src.index.github_repos` for federated search/lookup."""

from __future__ import annotations

import re
from typing import Any

from src.index._federated.registry import EntityRecord, Hit, register

_RE_GH_REPO_URL = re.compile(
    r"https?://(?:www\.)?github\.com/([^/\s?#]+)/([^/\s?#]+)",
    re.I,
)
_RE_GH_NS_URL = re.compile(
    r"https?://(?:www\.)?github\.com/([^/\s?#]+)/?$",
    re.I,
)


class GitHubReposAdapter:
    name = "github_repos"
    entity_types = ["repos"]

    def search(
        self,
        *,
        query: str,
        entity_type: str | None,  # noqa: ARG002 — single-type index
        top_k: int,
        filters: dict[str, Any] | None,
    ) -> list[Hit]:
        try:
            from src.index.github_repos.config import load_config
            from src.index.github_repos.retrieval.semantic import semantic_search
        except Exception:  # noqa: BLE001
            return []
        cfg = load_config()
        try:
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
            repo_id = payload.get("repo_id") or payload.get("full_name")
            if not repo_id:
                continue
            out.append(Hit(
                index=self.name, entity_type="repo",
                id=str(repo_id),
                title=payload.get("description") or str(repo_id),
                score=float(r.get("rerank_score") or r.get("vector_score") or 0.0),
                summary=_summary(payload),
                url=f"https://github.com/{repo_id}",
                payload=payload,
            ))
        return out

    def lookup(self, identifier: str) -> list[EntityRecord]:
        s = identifier.strip().rstrip("/")
        repo_id = None
        m = _RE_GH_REPO_URL.search(s)
        if m:
            repo_id = f"{m.group(1)}/{m.group(2)}"
        elif "/" in s and not s.startswith("http"):
            author, _, repo = s.partition("/")
            if author and repo:
                repo_id = s
        if not repo_id:
            # Bare org slug → no lookup yet (no orgs table in github index)
            return []
        try:
            from src.index.github_repos.storage.duckdb_store import GitHubReposStore
        except Exception:  # noqa: BLE001
            return []
        store = GitHubReposStore.open()
        if hasattr(store, "fetch_repo"):
            row = store.fetch_repo(repo_id)
            if row is not None:
                return [EntityRecord(
                    index=self.name, entity_type="repo", id=repo_id,
                    data=row, url=f"https://github.com/{repo_id}",
                )]
        return []


def _summary(payload: dict[str, Any]) -> str | None:
    parts = [
        str(payload.get("description") or ""),
        str(payload.get("language") or ""),
        str(payload.get("license") or ""),
    ]
    parts = [p for p in parts if p]
    return " — ".join(parts) if parts else None


register(GitHubReposAdapter())
