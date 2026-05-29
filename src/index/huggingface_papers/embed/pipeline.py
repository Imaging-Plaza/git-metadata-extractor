"""Embed huggingface_papers rows into the `huggingface_papers` Qdrant collection."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from src.index._github_accounts_base.embed_base import embed_accounts_async

if TYPE_CHECKING:
    from src.index.huggingface_papers.config import HuggingFacePapersIndexConfig
    from src.index.huggingface_papers.storage.duckdb_store import (
        HuggingFacePapersStore,
    )

LOGGER = logging.getLogger(__name__)

PAPERS_COLLECTION = "huggingface_papers"
ENTITY_TYPE = "papers"


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


def _row_to_text(row: dict[str, Any]) -> str:
    """Compose embedding text for a paper row.

    Order matters: title first (highest signal), then the HF AI
    summary if present (compact + curated), then the full abstract,
    then keywords + author names. Papers without an abstract still
    get a useful embedding from title + AI summary + keywords.
    """
    parts: list[str] = [str(row["title"])]
    ai_summary = row.get("ai_summary")
    if isinstance(ai_summary, str) and ai_summary.strip():
        parts.append(ai_summary.strip())
    summary = row.get("summary")
    if isinstance(summary, str) and summary.strip():
        parts.append(summary.strip())
    ai_keywords = _parse_json_list(row.get("ai_keywords"))
    keyword_strings = [str(k) for k in ai_keywords if isinstance(k, str) and k]
    if keyword_strings:
        parts.append("keywords: " + ", ".join(keyword_strings))
    authors = _parse_json_list(row.get("authors"))
    author_names = []
    for a in authors:
        if isinstance(a, dict):
            name = a.get("name")
            if isinstance(name, str) and name.strip():
                author_names.append(name.strip())
    if author_names:
        parts.append("authors: " + ", ".join(author_names))
    return "\n\n".join(parts)


def _row_to_payload(row: dict[str, Any]) -> dict[str, Any]:
    published = row.get("published_at")
    return {
        "entity_type": ENTITY_TYPE,
        "entity_id": row["arxiv_id"],
        "arxiv_id": row["arxiv_id"],
        "doi": row.get("doi"),
        "title": row.get("title"),
        "upvotes": row.get("upvotes"),
        "num_comments": row.get("num_comments"),
        "published_at": published.isoformat() if hasattr(published, "isoformat") else published,
    }


def embed_papers(
    *,
    config: HuggingFacePapersIndexConfig,
    store: HuggingFacePapersStore,
    limit: int | None = None,
) -> dict[str, int]:
    """Synchronously embed un-embedded papers into Qdrant."""
    chunks = asyncio.run(
        embed_accounts_async(
            config=config,
            conn=store.connect(),
            table="papers",
            id_column="arxiv_id",
            entity_type=ENTITY_TYPE,
            collection=PAPERS_COLLECTION,
            compose_text=_row_to_text,
            build_payload=_row_to_payload,
            limit=limit,
            # Papers config uses `config.huggingface.min_card_chars`,
            # so pass the threshold explicitly rather than letting the
            # shared helper reach into `config.github` (which doesn't
            # exist on this config).
            min_card_chars=config.huggingface.min_card_chars,
        ),
    )
    return {"papers": chunks}
