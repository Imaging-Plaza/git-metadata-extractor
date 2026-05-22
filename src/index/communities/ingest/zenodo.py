"""Fetch community metadata from Zenodo's `/api/communities` endpoint.

Two acquisition modes:

  ``fetch_by_slug(slug)`` — direct lookup; canonical when the slug is
  known (e.g. `cernopenlab`).

  ``discover_by_query(keyword)`` — paginated `?q=<keyword>` search,
  used to enumerate every community whose title contains an org token
  (e.g. `EPFL`). Auto-discovery so we don't have to babysit the slug
  list when new labs publish.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Iterator

import requests

logger = logging.getLogger(__name__)

_ZENODO_BASE = "https://zenodo.org/api/communities"
_REQUEST_TIMEOUT = 20.0
_PAGE_SIZE = 50
_RETRY_DELAY_SECONDS = 1.5


def _strip_html(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return re.sub(r"<[^>]+>", "", value).strip() or None


def _normalize_record(payload: dict[str, Any], parent_org: str | None) -> dict[str, Any]:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    slug = payload.get("slug") or payload.get("id")
    if not slug:
        return {}
    curator_names: list[str] = []
    curators = metadata.get("curation_policy") if isinstance(metadata, dict) else None
    if isinstance(curators, str) and curators.strip():
        # Zenodo doesn't expose structured curators on /api/communities;
        # the field is free-text. Keep first 200 chars as a single entry.
        curator_names.append(curators[:200])
    keywords: list[str] = []
    for key in ("subjects", "topics", "keywords"):
        val = metadata.get(key) if isinstance(metadata, dict) else None
        if isinstance(val, list):
            keywords.extend(str(v) for v in val if isinstance(v, (str, dict)))
    return {
        "community_id": f"zenodo:{slug}",
        "source": "zenodo",
        "source_slug": slug,
        "parent_org": parent_org,
        "title": (
            (metadata.get("title") if isinstance(metadata, dict) else None)
            or payload.get("title")
        ),
        "description": _strip_html(
            metadata.get("description") if isinstance(metadata, dict) else None,
        ),
        "url": payload.get("links", {}).get("self_html") if isinstance(payload.get("links"), dict) else None,
        "visibility": payload.get("access", {}).get("visibility") if isinstance(payload.get("access"), dict) else None,
        "created_at": payload.get("created"),
        "updated_at": payload.get("updated"),
        "curator_names": curator_names,
        "member_count": None,
        "record_count": payload.get("metadata", {}).get("size") if isinstance(payload.get("metadata"), dict) else None,
        "keywords": keywords,
        "raw": payload,
    }


def fetch_by_slug(slug: str, parent_org: str | None = None) -> dict[str, Any] | None:
    """Direct lookup; returns None on 404 / non-2xx."""
    url = f"{_ZENODO_BASE}/{slug}"
    try:
        response = requests.get(url, timeout=_REQUEST_TIMEOUT)
    except Exception:  # noqa: BLE001
        logger.exception("communities.ingest.zenodo: fetch_by_slug failed (%s)", slug)
        return None
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        logger.info(
            "communities.ingest.zenodo: %s returned %d", slug, response.status_code,
        )
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    return _normalize_record(payload, parent_org)


def discover_by_query(
    keyword: str,
    *,
    parent_org: str | None = None,
    page_size: int = _PAGE_SIZE,
    max_pages: int = 5,
) -> Iterator[dict[str, Any]]:
    """Iterate every Zenodo community whose `?q=<keyword>` matches."""
    page = 1
    while page <= max_pages:
        params = {"q": keyword, "size": page_size, "page": page, "sort": "bestmatch"}
        try:
            response = requests.get(_ZENODO_BASE, params=params, timeout=_REQUEST_TIMEOUT)
        except Exception:  # noqa: BLE001
            logger.exception(
                "communities.ingest.zenodo: discover_by_query failed (%s, page=%d)",
                keyword, page,
            )
            return
        if response.status_code != 200:
            logger.info(
                "communities.ingest.zenodo: discover %s page=%d returned %d",
                keyword, page, response.status_code,
            )
            return
        try:
            hits = response.json().get("hits", {}).get("hits", [])
        except ValueError:
            return
        if not hits:
            return
        for payload in hits:
            record = _normalize_record(payload, parent_org)
            if record:
                yield record
        if len(hits) < page_size:
            return
        page += 1
        time.sleep(_RETRY_DELAY_SECONDS)
