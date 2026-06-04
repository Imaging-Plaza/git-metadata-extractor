"""Fetch + persist one GitHub user card."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from src.index.github_users.models import UserRecord
from src.v2.canonicalization.github import github_user_iri

if TYPE_CHECKING:
    from src.index.github_repos.ingest.github_client import GitHubClient
    from src.index.github_users.config import GitHubUsersIndexConfig
    from src.index.github_users.storage.duckdb_store import GitHubUsersStore

LOGGER = logging.getLogger(__name__)


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _record_from_payload(login: str, payload: dict[str, Any]) -> UserRecord:
    return UserRecord(
        login=github_user_iri(login) or login,
        github_id=int(payload["id"]) if isinstance(payload.get("id"), int) else None,
        node_id=payload.get("node_id"),
        name=payload.get("name"),
        bio=payload.get("bio"),
        company=payload.get("company"),
        blog=payload.get("blog"),
        location=payload.get("location"),
        email=payload.get("email"),
        twitter_username=payload.get("twitter_username"),
        hireable=payload.get("hireable") if isinstance(payload.get("hireable"), bool) else None,
        public_repos=int(payload.get("public_repos") or 0),
        public_gists=int(payload.get("public_gists") or 0),
        followers=int(payload.get("followers") or 0),
        following=int(payload.get("following") or 0),
        account_type=payload.get("type"),
        avatar_url=payload.get("avatar_url"),
        html_url=payload.get("html_url"),
        created_at=_parse_iso(payload.get("created_at")),
        updated_at=_parse_iso(payload.get("updated_at")),
        raw=payload,
    )


def ingest_single_user(
    *,
    config: GitHubUsersIndexConfig,
    store: GitHubUsersStore,
    client: GitHubClient,
    login: str,
) -> str:
    """Fetch + upsert one user. Returns ``"ingested" | "skipped_404" | "skipped_org"``.

    ``GET /users/{login}`` returns both users and organisations (GitHub's
    namespace is unified). If the payload's `type` is `Organization` we
    skip it — orgs belong in the github_organizations index.
    """
    del config  # required for symmetry with the repo-index path
    payload = client.get_user(login)
    if not isinstance(payload, dict):
        LOGGER.warning("ingest skip: user not found or unreachable: %s", login)
        return "skipped_404"
    if payload.get("type") == "Organization":
        LOGGER.info(
            "ingest skip: %s is an Organization — belongs in github_organizations",
            login,
        )
        return "skipped_org"
    record = _record_from_payload(login, payload)
    store.upsert_user(record)
    LOGGER.info(
        "ingested user %s (name=%s public_repos=%d followers=%d)",
        login,
        record.name or "-",
        record.public_repos,
        record.followers,
    )
    return "ingested"
