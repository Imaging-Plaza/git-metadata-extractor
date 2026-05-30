"""Embed huggingface_models rows into the `huggingface_models` Qdrant collection."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from src.index._github_accounts_base.embed_base import embed_accounts_async

if TYPE_CHECKING:
    from src.index.huggingface_models.config import (
        HuggingFaceModelsIndexConfig,
    )
    from src.index.huggingface_models.storage.duckdb_store import (
        HuggingFaceModelsStore,
    )

LOGGER = logging.getLogger(__name__)

MODELS_COLLECTION = "huggingface_models"
ENTITY_TYPE = "models"


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
    """Compose embedding text for a model row.

    Models on HF rarely ship a free-text description outside the
    README, so we lean on what's structured: repo_id, library_name,
    pipeline_tag, tags, plus the card's `model-index.name` /
    `description` if present.
    """
    parts: list[str] = [str(row["repo_id"])]
    for key in ("library_name", "pipeline_tag", "license"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    card = _parse_json_dict(row.get("card_data"))
    description = card.get("description") if isinstance(card, dict) else None
    if isinstance(description, str) and description.strip():
        parts.append(description.strip())
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
        "pipeline_tag": row.get("pipeline_tag"),
        "library_name": row.get("library_name"),
        "license": row.get("license"),
        "downloads": row.get("downloads"),
        "likes": row.get("likes"),
    }


def embed_models(
    *,
    config: HuggingFaceModelsIndexConfig,
    store: HuggingFaceModelsStore,
    limit: int | None = None,
) -> dict[str, int]:
    """Synchronously embed un-embedded models into Qdrant."""
    chunks = asyncio.run(
        embed_accounts_async(
            config=config,
            conn=store.connect(),
            table="models",
            id_column="repo_id",
            entity_type=ENTITY_TYPE,
            collection=MODELS_COLLECTION,
            compose_text=_row_to_text,
            build_payload=_row_to_payload,
            limit=limit,
            min_card_chars=config.huggingface.min_card_chars,
        ),
    )
    return {"models": chunks}
