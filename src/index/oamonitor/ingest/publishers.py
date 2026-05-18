"""Ingest OAM-CH Publishers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.index.oamonitor.ingest._common import (
    _coalesce_str,
    oa_color_label,
    parse_iso_datetime,
)

if TYPE_CHECKING:
    from src.index.oamonitor.config import OamonitorIndexConfig
    from src.index.oamonitor.ingest.oamonitor_client import OamonitorClient
    from src.index.oamonitor.storage.duckdb_store import OamonitorStore

LOGGER = logging.getLogger(__name__)
COLLECTION = "Publishers"


def _row_from_doc(doc: dict[str, Any]) -> dict[str, Any] | None:
    publisher_id = _coalesce_str(doc.get("_id"))
    if publisher_id is None:
        return None
    name = _coalesce_str(doc.get("name"))
    oa_color = doc.get("oa_color")
    color_label = oa_color_label(oa_color)
    pieces: list[str] = []
    if name:
        pieces.append(name)
    if color_label:
        pieces.append(f"Open Access: {color_label}")
    embedding_text = " | ".join(pieces) if pieces else None
    return {
        "_id": publisher_id,
        "name": name,
        "oa_color": oa_color if isinstance(oa_color, int) else None,
        "updated": parse_iso_datetime(doc.get("updated")),
        "embedding_text": embedding_text,
        "raw": doc,
    }


def ingest_single_publisher(
    *,
    config: OamonitorIndexConfig,  # noqa: ARG001
    client: OamonitorClient,
    store: OamonitorStore,
    publisher_id: str,
) -> str:
    doc = client.find_one(COLLECTION, _id=publisher_id)
    if doc is None:
        return "not_found"
    row = _row_from_doc(doc)
    if row is None:
        return "rejected"
    store.upsert_publisher(row)
    return "persisted"


def ingest_publishers(
    *,
    config: OamonitorIndexConfig,
    client: OamonitorClient,
    store: OamonitorStore,
    filter_payload: dict[str, Any] | None = None,
    limit: int | None = None,
    skip: int = 0,
) -> int:
    persisted = 0
    for doc in client.iter_documents(
        COLLECTION, filter=filter_payload, limit=limit, skip=skip,
    ):
        row = _row_from_doc(doc)
        if row is None:
            continue
        store.upsert_publisher(row)
        persisted += 1
        if persisted % 500 == 0:
            LOGGER.info("oamonitor publishers: %d upserted", persisted)
    LOGGER.info("oamonitor publishers: total upserted=%d", persisted)
    return persisted


__all__ = ["COLLECTION", "ingest_publishers", "ingest_single_publisher"]
