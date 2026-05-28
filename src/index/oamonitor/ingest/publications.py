"""Ingest OAM-CH Publications."""

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
COLLECTION = "Publications"


def _organisation_ids(doc: dict[str, Any]) -> list[str]:
    """Extract organisation ``_id``s from the embedded ``organisations`` array."""
    raw = doc.get("organisations")
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        org_id = _coalesce_str(entry.get("_id"))
        if not org_id or org_id in seen:
            continue
        seen.add(org_id)
        out.append(org_id)
    return out


def _row_from_doc(doc: dict[str, Any]) -> dict[str, Any] | None:
    pub_id = _coalesce_str(doc.get("_id"))
    if pub_id is None:
        return None
    doi = _coalesce_str(doc.get("doi"))
    url = _coalesce_str(doc.get("url"))
    oa_color = doc.get("oa_color")
    license_ = _coalesce_str(doc.get("license"))
    published_date = doc.get("published_date")
    published_year = (
        int(published_date["year"])
        if isinstance(published_date, dict)
        and isinstance(published_date.get("year"), int)
        else None
    )
    publisher = doc.get("publisher") if isinstance(doc.get("publisher"), dict) else {}
    publisher_id = _coalesce_str(publisher.get("_id"))
    publisher_name = _coalesce_str(publisher.get("name"))
    source = doc.get("source") if isinstance(doc.get("source"), dict) else {}
    source_id = _coalesce_str(source.get("_id"))
    source_title = _coalesce_str(source.get("title"))
    organisation_ids = _organisation_ids(doc)

    pieces: list[str] = []
    if source_title:
        pieces.append(source_title)
    if doi:
        pieces.append(f"DOI: {doi}")
    if publisher_name:
        pieces.append(f"Publisher: {publisher_name}")
    if published_year is not None:
        pieces.append(f"Year: {published_year}")
    color_label = oa_color_label(oa_color)
    if color_label:
        pieces.append(f"Open Access: {color_label}")
    embedding_text = " | ".join(pieces) if pieces else None

    return {
        "_id": pub_id,
        "doi": doi,
        "url": url,
        "oa_color": oa_color if isinstance(oa_color, int) else None,
        "license": license_,
        "published_year": published_year,
        "publisher_id": publisher_id,
        "publisher_name": publisher_name,
        "source_id": source_id,
        "source_title": source_title,
        "organisation_ids": organisation_ids,
        "updated": parse_iso_datetime(doc.get("updated")),
        "embedding_text": embedding_text,
        "raw": doc,
    }


def ingest_single_publication(
    *,
    config: OamonitorIndexConfig,  # noqa: ARG001
    client: OamonitorClient,
    store: OamonitorStore,
    publication_id: str,
) -> str:
    doc = client.find_one(COLLECTION, _id=publication_id)
    if doc is None:
        return "not_found"
    row = _row_from_doc(doc)
    if row is None:
        return "rejected"
    store.upsert_publication(row)
    return "persisted"


def ingest_publications(
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
        store.upsert_publication(row)
        persisted += 1
        if persisted % 500 == 0:
            LOGGER.info("oamonitor publications: %d upserted", persisted)
    LOGGER.info("oamonitor publications: total upserted=%d", persisted)
    return persisted


__all__ = ["COLLECTION", "ingest_publications", "ingest_single_publication"]
