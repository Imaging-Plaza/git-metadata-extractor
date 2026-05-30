"""Embed huggingface_users rows into the `huggingface_users` Qdrant collection."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from src.index._github_accounts_base.embed_base import embed_accounts_async

if TYPE_CHECKING:
    from src.index.huggingface_users.config import (
        HuggingFaceUsersIndexConfig,
    )
    from src.index.huggingface_users.storage.duckdb_store import (
        HuggingFaceUsersStore,
    )

LOGGER = logging.getLogger(__name__)

USERS_COLLECTION = "huggingface_users"
ENTITY_TYPE = "users"


def _row_to_text(row: dict[str, Any]) -> str:
    """Compose embedding text for an HF user row.

    User namespaces are sparse — slug + fullname + details (bio).
    """
    parts: list[str] = [str(row["slug"])]
    for key in ("fullname", "details"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    return "\n".join(parts)


def _row_to_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "entity_type": ENTITY_TYPE,
        "entity_id": row["slug"],
        "slug": row["slug"],
        "fullname": row.get("fullname"),
        "num_models": row.get("num_models"),
        "num_datasets": row.get("num_datasets"),
        "num_spaces": row.get("num_spaces"),
        "num_followers": row.get("num_followers"),
    }


def embed_users(
    *,
    config: HuggingFaceUsersIndexConfig,
    store: HuggingFaceUsersStore,
    limit: int | None = None,
) -> dict[str, int]:
    chunks = asyncio.run(
        embed_accounts_async(
            config=config,
            conn=store.connect(),
            table="users",
            id_column="slug",
            entity_type=ENTITY_TYPE,
            collection=USERS_COLLECTION,
            compose_text=_row_to_text,
            build_payload=_row_to_payload,
            limit=limit,
            min_card_chars=config.huggingface.min_card_chars,
        ),
    )
    return {"users": chunks}
