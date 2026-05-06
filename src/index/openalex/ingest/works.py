"""Fetch + persist OpenAlex Works."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.index.openalex.ingest.openalex_client import batched, iter_works

if TYPE_CHECKING:
    from src.index.openalex.config import OpenAlexIndexConfig
    from src.index.openalex.storage.duckdb_store import DuckDBStore

LOGGER = logging.getLogger(__name__)


def reconstruct_abstract(inverted: dict[str, list[int]] | None) -> str | None:
    """Reconstruct plain-text abstract from OpenAlex's inverted index."""
    if not inverted:
        return None
    positions: list[tuple[int, str]] = []
    for token, idxs in inverted.items():
        for idx in idxs:
            positions.append((idx, token))
    positions.sort(key=lambda x: x[0])
    return " ".join(token for _, token in positions)


def _project_work(item: dict[str, Any]) -> dict[str, Any]:
    primary_topic = item.get("primary_topic") or {}
    primary_location = item.get("primary_location") or {}
    primary_source = (primary_location or {}).get("source") or {}
    return {
        "openalex_id": item.get("id"),
        "doi": item.get("doi"),
        "title": item.get("title"),
        "abstract": reconstruct_abstract(item.get("abstract_inverted_index")),
        "publication_year": item.get("publication_year"),
        "primary_topic_id": primary_topic.get("id"),
        "primary_source_id": primary_source.get("id"),
    }


def _author_links(item: dict[str, Any]) -> tuple[list[tuple[str, int]], list[str]]:
    """Return (author_id, position) and a flat list of institution_ids."""
    authorships = item.get("authorships") or []
    authors: list[tuple[str, int]] = []
    institutions: list[str] = []
    for position, authorship in enumerate(authorships):
        author = (authorship or {}).get("author") or {}
        author_id = author.get("id")
        if author_id:
            authors.append((author_id, position))
        for inst in (authorship or {}).get("institutions") or []:
            inst_id = inst.get("id")
            if inst_id:
                institutions.append(inst_id)
    return authors, institutions


def persist_work(store: DuckDBStore, item: dict[str, Any]) -> str | None:
    row = _project_work(item)
    work_id = row["openalex_id"]
    if not work_id:
        return None
    store.upsert_work(row, raw=item)
    authors, institutions = _author_links(item)
    if authors:
        store.upsert_work_authors(work_id, authors)
    if institutions:
        store.upsert_work_institutions(work_id, set(institutions))
    return work_id


def ingest_works(
    *,
    config: OpenAlexIndexConfig,
    store: DuckDBStore,
    filters: dict[str, Any],
    limit: int | None = None,
) -> int:
    count = 0
    last_logged = 0
    items = iter_works(config=config, filters=filters, limit=limit)
    for batch in batched(items, config.openalex.per_page):
        with store.transaction():
            for item in batch:
                if persist_work(store, item):
                    count += 1
        if count - last_logged >= 500:
            LOGGER.info("ingested %d works", count)
            last_logged = count
    LOGGER.info("works ingest complete: %d rows", count)
    return count
