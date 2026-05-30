"""Fetch + persist OpenAlex Sources (journals/venues/repositories)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.index.openalex.ingest.openalex_client import batched, iter_sources

if TYPE_CHECKING:
    from src.index.openalex.config import OpenAlexIndexConfig
    from src.index.openalex.storage.duckdb_store import OpenAlexStore

LOGGER = logging.getLogger(__name__)


def _project_source(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "openalex_id": item.get("id"),
        "issn_l": item.get("issn_l"),
        "display_name": item.get("display_name"),
        "type": item.get("type"),
    }


def ingest_sources(
    *,
    config: OpenAlexIndexConfig,
    store: OpenAlexStore,
    filters: dict[str, Any],
    limit: int | None = None,
) -> int:
    count = 0
    last_logged = 0
    items = iter_sources(config=config, filters=filters, limit=limit)
    for batch in batched(items, config.openalex.per_page):
        with store.transaction():
            for item in batch:
                row = _project_source(item)
                if not row["openalex_id"]:
                    continue
                store.upsert_source(row, raw=item)
                count += 1
        if count - last_logged >= 500:
            LOGGER.info("ingested %d sources", count)
            last_logged = count
    LOGGER.info("sources ingest complete: %d rows", count)
    return count
