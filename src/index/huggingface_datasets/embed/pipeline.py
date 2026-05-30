"""Embed huggingface_datasets rows into the `huggingface_datasets` Qdrant collection."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from src.index._github_accounts_base.embed_base import embed_accounts_async

if TYPE_CHECKING:
    from src.index.huggingface_datasets.config import (
        HuggingFaceDatasetsIndexConfig,
    )
    from src.index.huggingface_datasets.storage.duckdb_store import (
        HuggingFaceDatasetsStore,
    )

LOGGER = logging.getLogger(__name__)

DATASETS_COLLECTION = "huggingface_datasets"
ENTITY_TYPE = "datasets"


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
    """Compose embedding text for a dataset row.

    Datasets tend to ship richer card descriptions than models, so we
    lean on `card_data.description` + `card_data.pretty_name` + tags
    + license. The BibTeX citation text is also included — it carries
    paper titles and author names that make the embedding more
    discoverable for citation-based queries.
    """
    parts: list[str] = [str(row["repo_id"])]
    card = _parse_json_dict(row.get("card_data"))
    if isinstance(card, dict):
        pretty = card.get("pretty_name")
        if isinstance(pretty, str) and pretty.strip():
            parts.append(pretty.strip())
        description = card.get("description")
        if isinstance(description, str) and description.strip():
            parts.append(description.strip())
    license_ = row.get("license")
    if isinstance(license_, str) and license_.strip():
        parts.append(f"license: {license_.strip()}")
    citation = row.get("citation_text")
    if isinstance(citation, str) and citation.strip():
        parts.append(citation.strip())
    tags = _parse_json_list(row.get("tags"))
    tag_strings = [t for t in tags if isinstance(t, str) and t]
    if tag_strings:
        parts.append("tags: " + ", ".join(tag_strings))
    return "\n\n".join(parts)


def _row_to_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "entity_type": ENTITY_TYPE,
        "entity_id": row["repo_id"],
        "repo_id": row["repo_id"],
        "author": row.get("author"),
        "license": row.get("license"),
        "downloads": row.get("downloads"),
        "likes": row.get("likes"),
        "paperswithcode_url": row.get("paperswithcode_url"),
    }


def embed_datasets(
    *,
    config: HuggingFaceDatasetsIndexConfig,
    store: HuggingFaceDatasetsStore,
    limit: int | None = None,
) -> dict[str, int]:
    chunks = asyncio.run(
        embed_accounts_async(
            config=config,
            conn=store.connect(),
            table="datasets",
            id_column="repo_id",
            entity_type=ENTITY_TYPE,
            collection=DATASETS_COLLECTION,
            compose_text=_row_to_text,
            build_payload=_row_to_payload,
            limit=limit,
            min_card_chars=config.huggingface.min_card_chars,
        ),
    )
    return {"datasets": chunks}
