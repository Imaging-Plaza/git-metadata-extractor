"""Fetch + persist OpenAlex Authors."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.index.openalex.ingest.openalex_client import batched, iter_authors

if TYPE_CHECKING:
    from src.index.openalex.config import OpenAlexIndexConfig
    from src.index.openalex.storage.duckdb_store import DuckDBStore

LOGGER = logging.getLogger(__name__)


def _project_author(item: dict[str, Any]) -> dict[str, Any]:
    last_known = (item.get("last_known_institutions") or [None])[0] or {}
    return {
        "openalex_id": item.get("id"),
        "display_name": item.get("display_name"),
        "orcid": item.get("orcid"),
        "last_known_institution_id": last_known.get("id"),
    }


def ingest_authors(
    *,
    config: OpenAlexIndexConfig,
    store: DuckDBStore,
    filters: dict[str, Any],
    limit: int | None = None,
) -> int:
    count = 0
    last_logged = 0
    items = iter_authors(config=config, filters=filters, limit=limit)
    for batch in batched(items, config.openalex.per_page):
        with store.transaction():
            for item in batch:
                row = _project_author(item)
                if not row["openalex_id"]:
                    continue
                store.upsert_author(row, raw=item)
                count += 1
        if count - last_logged >= 500:
            LOGGER.info("ingested %d authors", count)
            last_logged = count
    LOGGER.info("authors ingest complete: %d rows", count)
    return count
