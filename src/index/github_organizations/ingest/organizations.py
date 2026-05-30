"""Fetch + persist one GitHub organization card."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from src.index.github_organizations.models import OrgRecord

if TYPE_CHECKING:
    from src.index.github_repos.ingest.github_client import GitHubClient
    from src.index.github_organizations.config import (
        GitHubOrganizationsIndexConfig,
    )
    from src.index.github_organizations.storage.duckdb_store import (
        GitHubOrganizationsStore,
    )

LOGGER = logging.getLogger(__name__)


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _record_from_payload(login: str, payload: dict[str, Any]) -> OrgRecord:
    return OrgRecord(
        login=login,
        github_id=int(payload["id"]) if isinstance(payload.get("id"), int) else None,
        node_id=payload.get("node_id"),
        name=payload.get("name"),
        description=payload.get("description"),
        blog=payload.get("blog"),
        location=payload.get("location"),
        email=payload.get("email"),
        twitter_username=payload.get("twitter_username"),
        company=payload.get("company"),
        public_repos=int(payload.get("public_repos") or 0),
        public_gists=int(payload.get("public_gists") or 0),
        followers=int(payload.get("followers") or 0),
        following=int(payload.get("following") or 0),
        is_verified=payload.get("is_verified") if isinstance(payload.get("is_verified"), bool) else None,
        has_organization_projects=payload.get("has_organization_projects")
        if isinstance(payload.get("has_organization_projects"), bool) else None,
        has_repository_projects=payload.get("has_repository_projects")
        if isinstance(payload.get("has_repository_projects"), bool) else None,
        account_type=payload.get("type"),
        avatar_url=payload.get("avatar_url"),
        html_url=payload.get("html_url"),
        created_at=_parse_iso(payload.get("created_at")),
        updated_at=_parse_iso(payload.get("updated_at")),
        raw=payload,
    )


def ingest_single_organization(
    *,
    config: GitHubOrganizationsIndexConfig,
    store: GitHubOrganizationsStore,
    client: GitHubClient,
    login: str,
) -> str:
    """Fetch + upsert one organization. Returns
    ``"ingested" | "skipped_404" | "skipped_user"``.

    ``GET /orgs/{org}`` only returns organisations; if GitHub returns a
    payload whose `type` is `User` (it shouldn't, but defensive), we
    skip it — users belong in the github_users index.
    """
    del config  # required for symmetry with the repo-index path
    payload = client.get_organization(login)
    if not isinstance(payload, dict):
        LOGGER.warning(
            "ingest skip: organization not found or unreachable: %s",
            login,
        )
        return "skipped_404"
    if payload.get("type") == "User":
        LOGGER.info(
            "ingest skip: %s is a User — belongs in github_users",
            login,
        )
        return "skipped_user"
    record = _record_from_payload(login, payload)
    store.upsert_organization(record)
    LOGGER.info(
        "ingested org %s (name=%s public_repos=%d followers=%d)",
        login,
        record.name or "-",
        record.public_repos,
        record.followers,
    )
    return "ingested"
