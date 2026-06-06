# src/index/_gitlab_base/user_ingest.py
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.index._gitlab_base.models import GitLabUserRecord
from src.v2.canonicalization.gitlab import gitlab_iri

if TYPE_CHECKING:
    from src.index._gitlab_base.client import GitLabClient
    from src.index._gitlab_base.user_store import GitLabUserStore


def _user_record_from_payload(host: str, payload: dict[str, Any]) -> GitLabUserRecord:
    username = payload.get("username") or ""
    web_url = payload.get("web_url") or gitlab_iri(host, "user", username)
    # Use web_url as user_id (canonical form); fallback to gitlab_iri
    user_id = web_url or gitlab_iri(host, "user", username)
    return GitLabUserRecord(
        user_id=user_id,
        host=host,
        username=username,
        name=payload.get("name"),
        bio=payload.get("bio"),
        location=payload.get("location"),
        organization=payload.get("organization"),
        job_title=payload.get("job_title"),
        public_email=payload.get("public_email"),
        website_url=payload.get("website_url"),
        linkedin=payload.get("linkedin"),
        twitter=payload.get("twitter"),
        avatar_url=payload.get("avatar_url"),
        web_url=payload.get("web_url"),
        raw=payload,
    )


def ingest_users(*, host: str, client: GitLabClient, store: GitLabUserStore,
                 limit: int | None = None) -> dict[str, int]:
    seen = 0
    for payload in client.iter_public_users():
        store.upsert_user(_user_record_from_payload(host, payload))
        seen += 1
        if limit and seen >= limit:
            break
    return {"seen": seen}
