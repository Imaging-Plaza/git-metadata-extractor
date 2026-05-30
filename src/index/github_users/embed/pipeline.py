"""Embed github_users rows into the `github_users` Qdrant collection."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from src.index._github_accounts_base.embed_base import embed_accounts_async

if TYPE_CHECKING:
    from src.index.github_users.config import GitHubUsersIndexConfig
    from src.index.github_users.storage.duckdb_store import GitHubUsersStore

LOGGER = logging.getLogger(__name__)

USERS_COLLECTION = "github_users"
ENTITY_TYPE = "users"


def _row_to_text(row: dict[str, Any]) -> str:
    """Compose embedding text for a user row.

    User cards are short, so we concatenate every signal we have:
    login, name, bio, company, location, blog. The login goes first so
    a query that mentions the handle scores well even when the rest of
    the card is sparse.
    """
    parts: list[str] = [str(row["login"])]
    for key in ("name", "bio", "company", "location", "blog"):
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
        "company": row.get("company"),
        "location": row.get("location"),
        "public_repos": row.get("public_repos"),
        "followers": row.get("followers"),
        "html_url": row.get("html_url"),
        "created_at": created.isoformat() if hasattr(created, "isoformat") else created,
    }


def embed_users(
    *,
    config: GitHubUsersIndexConfig,
    store: GitHubUsersStore,
    limit: int | None = None,
) -> dict[str, int]:
    """Synchronously embed un-embedded users into Qdrant."""
    chunks = asyncio.run(
        embed_accounts_async(
            config=config,
            conn=store.connect(),
            table="users",
            id_column="login",
            entity_type=ENTITY_TYPE,
            collection=USERS_COLLECTION,
            compose_text=_row_to_text,
            build_payload=_row_to_payload,
            limit=limit,
        ),
    )
    return {"users": chunks}
