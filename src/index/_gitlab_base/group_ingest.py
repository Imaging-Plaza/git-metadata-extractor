# src/index/_gitlab_base/group_ingest.py
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.index._gitlab_base.models import GitLabGroupRecord
from src.v2.canonicalization.gitlab import gitlab_iri

if TYPE_CHECKING:
    from src.index._gitlab_base.client import GitLabClient
    from src.index._gitlab_base.group_store import GitLabGroupStore


def _group_record_from_payload(host: str, payload: dict[str, Any]) -> GitLabGroupRecord:
    full_path = payload.get("full_path") or ""
    web_url = payload.get("web_url") or gitlab_iri(host, "group", full_path)
    # Use web_url as group_id (canonical form); fallback to gitlab_iri
    group_id = web_url or gitlab_iri(host, "group", full_path)
    return GitLabGroupRecord(
        group_id=group_id,
        host=host,
        full_path=full_path,
        name=payload.get("name"),
        description=payload.get("description"),
        visibility=payload.get("visibility"),
        parent=None,  # parent_id lookup deferred; web_url of parent not in basic payload
        web_url=payload.get("web_url"),
        raw=payload,
    )


def ingest_groups(*, host: str, client: GitLabClient, store: GitLabGroupStore,
                  limit: int | None = None) -> dict[str, int]:
    seen = 0
    for payload in client.iter_public_groups():
        store.upsert_group(_group_record_from_payload(host, payload))
        seen += 1
        if limit and seen >= limit:
            break
    return {"seen": seen}
