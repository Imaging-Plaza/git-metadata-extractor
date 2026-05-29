"""Fetch + persist OpenAlex Topics."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.index.openalex.ingest.openalex_client import batched, iter_topics

if TYPE_CHECKING:
    from src.index.openalex.config import OpenAlexIndexConfig
    from src.index.openalex.storage.duckdb_store import OpenAlexStore

LOGGER = logging.getLogger(__name__)


def _project_topic(item: dict[str, Any]) -> dict[str, Any]:
    domain = item.get("domain") or {}
    field = item.get("field") or {}
    return {
        "openalex_id": item.get("id"),
        "display_name": item.get("display_name"),
        "domain_id": domain.get("id"),
        "field_id": field.get("id"),
    }


def ingest_topics(
    *,
    config: OpenAlexIndexConfig,
    store: OpenAlexStore,
    filters: dict[str, Any] | None = None,
    limit: int | None = None,
) -> int:
    count = 0
    last_logged = 0
    items = iter_topics(config=config, filters=filters, limit=limit)
    for batch in batched(items, config.openalex.per_page):
        with store.transaction():
            for item in batch:
                row = _project_topic(item)
                if not row["openalex_id"]:
                    continue
                store.upsert_topic(row, raw=item)
                count += 1
        if count - last_logged >= 500:
            LOGGER.info("ingested %d topics", count)
            last_logged = count
    LOGGER.info("topics ingest complete: %d rows", count)
    return count
