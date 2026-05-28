"""Ingest OAM-CH Journals."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.index.oamonitor.ingest._common import (
    _coalesce_list_of_str,
    _coalesce_str,
    oa_color_label,
    parse_iso_datetime,
)

if TYPE_CHECKING:
    from src.index.oamonitor.config import OamonitorIndexConfig
    from src.index.oamonitor.ingest.oamonitor_client import OamonitorClient
    from src.index.oamonitor.storage.duckdb_store import OamonitorStore

LOGGER = logging.getLogger(__name__)
COLLECTION = "Journals"


def _row_from_doc(doc: dict[str, Any]) -> dict[str, Any] | None:
    journal_id = _coalesce_str(doc.get("_id"))
    if journal_id is None:
        return None
    title = _coalesce_str(doc.get("title"))
    issns = _coalesce_list_of_str(doc.get("issns"))
    oa_color = doc.get("oa_color")
    color_label = oa_color_label(oa_color)
    pieces: list[str] = []
    if title:
        pieces.append(title)
    if issns:
        pieces.append("ISSN: " + ", ".join(issns))
    if color_label:
        pieces.append(f"Open Access: {color_label}")
    embedding_text = " | ".join(pieces) if pieces else None
    return {
        "_id": journal_id,
        "title": title,
        "oa_color": oa_color if isinstance(oa_color, int) else None,
        "issns": issns,
        "updated": parse_iso_datetime(doc.get("updated")),
        "embedding_text": embedding_text,
        "raw": doc,
    }


def ingest_single_journal(
    *,
    config: OamonitorIndexConfig,  # noqa: ARG001 — kept for signature parity
    client: OamonitorClient,
    store: OamonitorStore,
    journal_id: str,
) -> str:
    """Fetch + upsert one journal by ``_id``. Returns the outcome string."""
    doc = client.find_one(COLLECTION, _id=journal_id)
    if doc is None:
        return "not_found"
    row = _row_from_doc(doc)
    if row is None:
        return "rejected"
    store.upsert_journal(row)
    return "persisted"


def ingest_journals(
    *,
    config: OamonitorIndexConfig,
    client: OamonitorClient,
    store: OamonitorStore,
    filter_payload: dict[str, Any] | None = None,
    limit: int | None = None,
    skip: int = 0,
) -> int:
    """Bulk-stream Journals into the local store. Returns rows upserted."""
    persisted = 0
    for doc in client.iter_documents(
        COLLECTION, filter=filter_payload, limit=limit, skip=skip,
    ):
        row = _row_from_doc(doc)
        if row is None:
            continue
        store.upsert_journal(row)
        persisted += 1
        if persisted % 500 == 0:
            LOGGER.info("oamonitor journals: %d upserted", persisted)
    LOGGER.info("oamonitor journals: total upserted=%d", persisted)
    return persisted


__all__ = ["COLLECTION", "ingest_journals", "ingest_single_journal"]
