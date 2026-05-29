"""Embed github_organizations rows into the `github_organizations` Qdrant collection."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from src.index._github_accounts_base.embed_base import embed_accounts_async

if TYPE_CHECKING:
    from src.index.github_organizations.config import (
        GitHubOrganizationsIndexConfig,
    )
    from src.index.github_organizations.storage.duckdb_store import (
        GitHubOrganizationsStore,
    )

LOGGER = logging.getLogger(__name__)

ORGS_COLLECTION = "github_organizations"
ENTITY_TYPE = "organizations"


def _row_to_text(row: dict[str, Any]) -> str:
    """Compose embedding text for an org row.

    Org cards carry slightly different signals than user cards: there's
    no `bio` field — instead `description` plays that role. We also
    pull `name` because GitHub orgs frequently have a display name
    that differs from the handle (e.g. login `EPFL-ENAC`, name
    "EPFL School of Architecture, Civil and Environmental Engineering").
    """
    parts: list[str] = [str(row["login"])]
    for key in ("name", "description", "location", "blog"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    return "\n".join(parts)


def _row_to_payload(row: dict[str, Any]) -> dict[str, Any]:
    created = row.get("created_at")
    return {
        "entity_type": ENTITY_TYPE,
        "entity_id": row["login"],
        "login": row["login"],
        "github_id": row.get("github_id"),
        "name": row.get("name"),
        "location": row.get("location"),
        "public_repos": row.get("public_repos"),
        "followers": row.get("followers"),
        "is_verified": row.get("is_verified"),
        "html_url": row.get("html_url"),
        "created_at": created.isoformat() if hasattr(created, "isoformat") else created,
    }


def embed_organizations(
    *,
    config: GitHubOrganizationsIndexConfig,
    store: GitHubOrganizationsStore,
    limit: int | None = None,
) -> dict[str, int]:
    """Synchronously embed un-embedded organizations into Qdrant."""
    chunks = asyncio.run(
        embed_accounts_async(
            config=config,
            conn=store.connect(),
            table="organizations",
            id_column="login",
            entity_type=ENTITY_TYPE,
            collection=ORGS_COLLECTION,
            compose_text=_row_to_text,
            build_payload=_row_to_payload,
            limit=limit,
        ),
    )
    return {"organizations": chunks}
