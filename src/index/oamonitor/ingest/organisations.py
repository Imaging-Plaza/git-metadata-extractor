"""Ingest OAM-CH Organisations."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.index.oamonitor.ingest._common import (
    _coalesce_list_of_str,
    _coalesce_str,
    _label_strings,
    parse_iso_datetime,
)

if TYPE_CHECKING:
    from src.index.oamonitor.config import OamonitorIndexConfig
    from src.index.oamonitor.ingest.oamonitor_client import OamonitorClient
    from src.index.oamonitor.storage.duckdb_store import OamonitorStore

LOGGER = logging.getLogger(__name__)
COLLECTION = "Organisations"


def _row_from_doc(doc: dict[str, Any]) -> dict[str, Any] | None:
    org_id = _coalesce_str(doc.get("_id"))
    if org_id is None:
        return None
    name = _coalesce_str(doc.get("name"))
    org_type = _coalesce_str(doc.get("type"))
    grid_id = _coalesce_str(doc.get("grid_id"))
    address = doc.get("address") if isinstance(doc.get("address"), dict) else {}
    country_code = _coalesce_str(address.get("country_code")) if address else None
    acronyms = _coalesce_list_of_str(doc.get("acronyms"))
    aliases = _coalesce_list_of_str(doc.get("aliases"))
    labels = _label_strings(doc.get("labels"))
    pieces: list[str] = []
    if name:
        pieces.append(name)
    if acronyms:
        pieces.append("Acronyms: " + ", ".join(acronyms))
    if aliases:
        pieces.append("Aliases: " + ", ".join(aliases))
    if labels:
        pieces.append("Labels: " + ", ".join(labels))
    if org_type:
        pieces.append(f"Type: {org_type}")
    if country_code:
        pieces.append(f"Country: {country_code}")
    embedding_text = " | ".join(pieces) if pieces else None
    return {
        "_id": org_id,
        "name": name,
        "type": org_type,
        "grid_id": grid_id,
        "country_code": country_code,
        "acronyms": acronyms,
        "aliases": aliases,
        "updated": parse_iso_datetime(doc.get("updated")),
        "embedding_text": embedding_text,
        "raw": doc,
    }


def ingest_single_organisation(
    *,
    config: OamonitorIndexConfig,  # noqa: ARG001
    client: OamonitorClient,
    store: OamonitorStore,
    organisation_id: str,
) -> str:
    doc = client.find_one(COLLECTION, _id=organisation_id)
    if doc is None:
        return "not_found"
    row = _row_from_doc(doc)
    if row is None:
        return "rejected"
    store.upsert_organisation(row)
    return "persisted"


def ingest_organisations(
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
        store.upsert_organisation(row)
        persisted += 1
        if persisted % 500 == 0:
            LOGGER.info("oamonitor organisations: %d upserted", persisted)
    LOGGER.info("oamonitor organisations: total upserted=%d", persisted)
    return persisted


__all__ = ["COLLECTION", "ingest_organisations", "ingest_single_organisation"]
