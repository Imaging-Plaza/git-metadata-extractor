"""Fetch + persist OpenAlex Institutions."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.index.openalex.ingest.openalex_client import batched, iter_institutions

if TYPE_CHECKING:
    from src.index.openalex.config import OpenAlexIndexConfig
    from src.index.openalex.storage.duckdb_store import OpenAlexStore

LOGGER = logging.getLogger(__name__)


def _project_institution(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "openalex_id": item.get("id"),
        "ror": item.get("ror"),
        "display_name": item.get("display_name"),
        "country_code": item.get("country_code"),
    }


def ingest_institutions(
    *,
    config: OpenAlexIndexConfig,
    store: OpenAlexStore,
    filters: dict[str, Any],
    limit: int | None = None,
) -> int:
    count = 0
    last_logged = 0
    items = iter_institutions(config=config, filters=filters, limit=limit)
    for batch in batched(items, config.openalex.per_page):
        with store.transaction():
            for item in batch:
                row = _project_institution(item)
                if not row["openalex_id"]:
                    continue
                store.upsert_institution(row, raw=item)
                count += 1
        if count - last_logged >= 500:
            LOGGER.info("ingested %d institutions", count)
            last_logged = count
    LOGGER.info("institutions ingest complete: %d rows", count)
    return count
