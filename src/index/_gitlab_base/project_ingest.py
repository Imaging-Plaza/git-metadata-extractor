# src/index/_gitlab_base/project_ingest.py
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.index._gitlab_base.models import GitLabProjectRecord
from src.v2.canonicalization.gitlab import gitlab_iri

if TYPE_CHECKING:
    from src.index._gitlab_base.client import GitLabClient
    from src.index._gitlab_base.project_store import GitLabProjectStore


def _project_record_from_payload(host: str, payload: dict[str, Any]) -> GitLabProjectRecord:
    full_path = payload.get("path_with_namespace") or ""
    web_url = payload.get("web_url") or gitlab_iri(host, "project", full_path)
    fork_parent = payload.get("forked_from_project") or None
    ns = payload.get("namespace") or {}
    return GitLabProjectRecord(
        project_id=web_url,
        host=host,
        full_path=full_path,
        name=payload.get("name"),
        description=payload.get("description"),
        visibility=payload.get("visibility"),
        is_fork=fork_parent is not None,
        forked_from=(fork_parent or {}).get("web_url") if isinstance(fork_parent, dict) else None,
        namespace=ns.get("full_path") if isinstance(ns, dict) else None,
        topics=list(payload.get("topics") or []),
        star_count=int(payload.get("star_count") or 0),
        forks_count=int(payload.get("forks_count") or 0),
        default_branch=payload.get("default_branch"),
        last_activity_at=payload.get("last_activity_at"),
        created_at=payload.get("created_at"),
        raw=payload,
    )


def ingest_projects(*, host: str, client: GitLabClient, store: GitLabProjectStore,
                    limit: int | None = None) -> dict[str, int]:
    seen = 0
    for payload in client.iter_public_projects():
        store.upsert_project(_project_record_from_payload(host, payload))
        seen += 1
        if limit and seen >= limit:
            break
    return {"seen": seen}
