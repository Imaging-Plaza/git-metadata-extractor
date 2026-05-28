"""OpenAlex enrichment for EPFL Graph categories.

Given OpenAlex topic IDs (resolved via
:func:`src.module.epfl_graph.ontology.category_nearest_openalex_topics`),
return small lists of related publications, people, and institutions.

Uses :mod:`pyalex` directly (already a project dep) instead of the
heavier ``src.index.openalex`` ingestion layer — we only need a handful
of records per topic, not pageable feeds.

OpenAlex polite-pool email is read from ``OPENALEX_MAILTO``; without it
calls fall back to the slower public pool. The first call sets the
global pyalex config; subsequent calls reuse it.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Iterable

import pyalex
from pyalex import Authors, Institutions, Works

logger = logging.getLogger(__name__)

_PYALEX_LOCK = threading.Lock()
_PYALEX_CONFIGURED = False


def _ensure_pyalex_configured() -> None:
    global _PYALEX_CONFIGURED  # noqa: PLW0603
    if _PYALEX_CONFIGURED:
        return
    with _PYALEX_LOCK:
        if _PYALEX_CONFIGURED:
            return
        mailto = (os.environ.get("OPENALEX_MAILTO") or "").strip() or None
        pyalex.config.email = mailto
        pyalex.config.api_key = None
        pyalex.config.max_retries = 3
        pyalex.config.retry_backoff_factor = 0.5
        pyalex.config.retry_http_codes = [429, 500, 502, 503, 504]
        _PYALEX_CONFIGURED = True


def _normalize_topic_ids(topic_ids: Iterable[str | int]) -> list[str]:
    out: list[str] = []
    for raw in topic_ids:
        if not isinstance(raw, (str, int)):
            continue
        s = str(raw).strip()
        if not s:
            continue
        # OpenAlex accepts both bare ids ("12345") and prefixed ("T12345").
        if not s.startswith(("T", "t")):
            s = "T" + s
        out.append(s.upper())
    return out


def publications_for_topics(
    topic_ids: Iterable[str | int],
    *,
    top_n: int = 5,
) -> list[dict[str, Any]]:
    """Return top-cited Works tagged with any of the given topic IDs."""
    ids = _normalize_topic_ids(topic_ids)
    if not ids:
        return []
    _ensure_pyalex_configured()
    try:
        query = (
            Works()
            .filter(**{"topics.id": "|".join(ids)})
            .sort(cited_by_count="desc")
            .select(
                "id,doi,title,publication_year,cited_by_count,"
                "authorships,primary_location,type",
            )
        )
        rows = list(query.get(per_page=top_n))[:top_n]
    except Exception as exc:  # noqa: BLE001
        logger.warning("openalex Works lookup failed: %s", exc)
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        primary = row.get("primary_location") or {}
        source = primary.get("source") if isinstance(primary, dict) else None
        out.append(
            {
                "openalex_id": row.get("id"),
                "doi": row.get("doi"),
                "title": row.get("title"),
                "year": row.get("publication_year"),
                "cited_by_count": row.get("cited_by_count"),
                "type": row.get("type"),
                "venue": source.get("display_name") if isinstance(source, dict) else None,
                "url": (
                    primary.get("landing_page_url")
                    if isinstance(primary, dict)
                    else None
                ),
            },
        )
    return out


def people_for_topics(
    topic_ids: Iterable[str | int],
    *,
    top_n: int = 5,
) -> list[dict[str, Any]]:
    """Return top-published Authors tagged with any of the given topic IDs."""
    ids = _normalize_topic_ids(topic_ids)
    if not ids:
        return []
    _ensure_pyalex_configured()
    try:
        query = (
            Authors()
            .filter(**{"topics.id": "|".join(ids)})
            .sort(works_count="desc")
            .select(
                "id,display_name,orcid,works_count,cited_by_count,"
                "last_known_institutions,affiliations",
            )
        )
        rows = list(query.get(per_page=top_n))[:top_n]
    except Exception as exc:  # noqa: BLE001
        logger.warning("openalex Authors lookup failed: %s", exc)
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        institutions = row.get("last_known_institutions") or []
        primary_inst = institutions[0] if institutions else {}
        out.append(
            {
                "openalex_id": row.get("id"),
                "name": row.get("display_name"),
                "orcid": row.get("orcid"),
                "works_count": row.get("works_count"),
                "cited_by_count": row.get("cited_by_count"),
                "institution": primary_inst.get("display_name") if isinstance(primary_inst, dict) else None,
                "ror": primary_inst.get("ror") if isinstance(primary_inst, dict) else None,
            },
        )
    return out


def units_for_topics(
    topic_ids: Iterable[str | int],
    *,
    top_n: int = 5,
) -> list[dict[str, Any]]:
    """Return top-publishing Institutions tagged with any of the given topic IDs."""
    ids = _normalize_topic_ids(topic_ids)
    if not ids:
        return []
    _ensure_pyalex_configured()
    try:
        query = (
            Institutions()
            .filter(**{"topics.id": "|".join(ids)})
            .sort(works_count="desc")
            .select(
                "id,display_name,ror,country_code,type,homepage_url,"
                "works_count,cited_by_count",
            )
        )
        rows = list(query.get(per_page=top_n))[:top_n]
    except Exception as exc:  # noqa: BLE001
        logger.warning("openalex Institutions lookup failed: %s", exc)
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "openalex_id": row.get("id"),
                "name": row.get("display_name"),
                "ror": row.get("ror"),
                "country_code": row.get("country_code"),
                "type": row.get("type"),
                "homepage_url": row.get("homepage_url"),
                "works_count": row.get("works_count"),
                "cited_by_count": row.get("cited_by_count"),
            },
        )
    return out
