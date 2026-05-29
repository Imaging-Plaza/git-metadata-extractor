"""Embed huggingface_spaces rows into the `huggingface_spaces` Qdrant collection."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from src.index._github_accounts_base.embed_base import embed_accounts_async

if TYPE_CHECKING:
    from src.index.huggingface_spaces.config import (
        HuggingFaceSpacesIndexConfig,
    )
    from src.index.huggingface_spaces.storage.duckdb_store import (
        HuggingFaceSpacesStore,
    )

LOGGER = logging.getLogger(__name__)

SPACES_COLLECTION = "huggingface_spaces"
ENTITY_TYPE = "spaces"


def _parse_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _parse_json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _row_to_text(row: dict[str, Any]) -> str:
    """Compose embedding text for a Space row.

    Spaces are interactive demos — the most useful text is the card's
    `title` + `short_description` + the SDK (so queries about
    "gradio app for X" surface relevant spaces) + tags. The
    `runtime_stage` is filterable, not embeddable.
    """
    parts: list[str] = [str(row["repo_id"])]
    card = _parse_json_dict(row.get("card_data"))
    if isinstance(card, dict):
        title = card.get("title")
        if isinstance(title, str) and title.strip():
            parts.append(title.strip())
        short = card.get("short_description") or card.get("description")
        if isinstance(short, str) and short.strip():
            parts.append(short.strip())
    sdk = row.get("sdk")
    if isinstance(sdk, str) and sdk.strip():
        parts.append(f"sdk: {sdk.strip()}")
    tags = _parse_json_list(row.get("tags"))
    tag_strings = [t for t in tags if isinstance(t, str) and t]
    if tag_strings:
        parts.append("tags: " + ", ".join(tag_strings))
    return "\n".join(parts)


def _row_to_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "entity_type": ENTITY_TYPE,
        "entity_id": row["repo_id"],
        "repo_id": row["repo_id"],
        "author": row.get("author"),
        "sdk": row.get("sdk"),
        "runtime_stage": row.get("runtime_stage"),
        "hardware": row.get("hardware"),
        "license": row.get("license"),
        "likes": row.get("likes"),
    }


def embed_spaces(
    *,
    config: HuggingFaceSpacesIndexConfig,
    store: HuggingFaceSpacesStore,
    limit: int | None = None,
) -> dict[str, int]:
    chunks = asyncio.run(
        embed_accounts_async(
            config=config,
            conn=store.connect(),
            table="spaces",
            id_column="repo_id",
            entity_type=ENTITY_TYPE,
            collection=SPACES_COLLECTION,
            compose_text=_row_to_text,
            build_payload=_row_to_payload,
            limit=limit,
            min_card_chars=config.huggingface.min_card_chars,
        ),
    )
    return {"spaces": chunks}
