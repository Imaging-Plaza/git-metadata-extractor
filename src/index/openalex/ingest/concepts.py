"""Fetch + persist OpenAlex Concepts (legacy)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.index.openalex.ingest.openalex_client import batched, iter_concepts

if TYPE_CHECKING:
    from src.index.openalex.config import OpenAlexIndexConfig
    from src.index.openalex.storage.duckdb_store import DuckDBStore

LOGGER = logging.getLogger(__name__)


def _project_concept(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "openalex_id": item.get("id"),
        "display_name": item.get("display_name"),
        "level": item.get("level"),
    }


def ingest_concepts(
    *,
    config: OpenAlexIndexConfig,
    store: DuckDBStore,
    filters: dict[str, Any] | None = None,
    limit: int | None = None,
) -> int:
    count = 0
    last_logged = 0
    items = iter_concepts(config=config, filters=filters, limit=limit)
    for batch in batched(items, config.openalex.per_page):
        with store.transaction():
            for item in batch:
                row = _project_concept(item)
                if not row["openalex_id"]:
                    continue
                store.upsert_concept(row, raw=item)
                count += 1
        if count - last_logged >= 500:
            LOGGER.info("ingested %d concepts", count)
            last_logged = count
    LOGGER.info("concepts ingest complete: %d rows", count)
    return count
