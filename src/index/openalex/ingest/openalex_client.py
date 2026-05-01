"""Thin wrapper around the `pyalex` package.

`pyalex` already handles polite-pool email injection, cursor pagination,
filter chaining, `select=` projections, and retry/backoff. This module's
only job is to apply our config to it once at import time and expose a
small set of typed iterators.
"""

from __future__ import annotations

import logging
from itertools import islice
from typing import TYPE_CHECKING, Any

import pyalex
from pyalex import Authors, Concepts, Institutions, Sources, Topics, Works

from src.index.openalex.config import OpenAlexIndexConfig
from src.index.openalex.models import (
    AUTHORS_PROJECTION,
    CONCEPTS_PROJECTION,
    INSTITUTIONS_PROJECTION,
    SOURCES_PROJECTION,
    TOPICS_PROJECTION,
    WORKS_PROJECTION,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

LOGGER = logging.getLogger(__name__)

_CONFIGURED = False


def configure_pyalex(config: OpenAlexIndexConfig) -> None:
    """Apply our config to the pyalex global. Idempotent."""
    global _CONFIGURED  # noqa: PLW0603
    config.require_ingest()
    pyalex.config.email = config.openalex.mailto
    pyalex.config.api_key = None
    pyalex.config.max_retries = 5
    pyalex.config.retry_backoff_factor = 0.5
    pyalex.config.retry_http_codes = [429, 500, 502, 503, 504]
    _CONFIGURED = True
    LOGGER.info("pyalex configured (mailto=%s)", config.openalex.mailto)


def _ensure_configured(config: OpenAlexIndexConfig) -> None:
    if not _CONFIGURED:
        configure_pyalex(config)


def batched(iterable: Any, size: int) -> Iterator[list]:
    """Yield successive `size`-sized chunks from `iterable`."""
    it = iter(iterable)
    while True:
        chunk = list(islice(it, size))
        if not chunk:
            return
        yield chunk


def _paginate(
    query: Any,
    *,
    per_page: int,
    limit: int | None,
) -> Iterator[dict[str, Any]]:
    yielded = 0
    pages = query.paginate(per_page=per_page, n_max=limit, method="cursor")
    for page in pages:
        for item in page:
            yield item
            yielded += 1
            if limit is not None and yielded >= limit:
                return


def iter_works(
    *,
    config: OpenAlexIndexConfig,
    filters: dict[str, Any],
    limit: int | None = None,
    extra_search: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield Work dicts matching the given filter dict.

    `extra_search` runs OpenAlex's `default.search` (title + abstract) on top
    of the filter. For `fulltext.search`, embed it inside `filters` as
    `{"fulltext": {"search": "..."}}`.
    """
    _ensure_configured(config)
    query = Works().filter(**filters).select(",".join(WORKS_PROJECTION))
    if extra_search:
        query = query.search(extra_search)
    yield from _paginate(query, per_page=config.openalex.per_page, limit=limit)


def iter_authors(
    *,
    config: OpenAlexIndexConfig,
    filters: dict[str, Any],
    limit: int | None = None,
) -> Iterator[dict[str, Any]]:
    _ensure_configured(config)
    query = Authors().filter(**filters).select(",".join(AUTHORS_PROJECTION))
    yield from _paginate(query, per_page=config.openalex.per_page, limit=limit)


def iter_institutions(
    *,
    config: OpenAlexIndexConfig,
    filters: dict[str, Any],
    limit: int | None = None,
) -> Iterator[dict[str, Any]]:
    _ensure_configured(config)
    query = (
        Institutions().filter(**filters).select(",".join(INSTITUTIONS_PROJECTION))
    )
    yield from _paginate(query, per_page=config.openalex.per_page, limit=limit)


def iter_sources(
    *,
    config: OpenAlexIndexConfig,
    filters: dict[str, Any],
    limit: int | None = None,
) -> Iterator[dict[str, Any]]:
    _ensure_configured(config)
    query = Sources().filter(**filters).select(",".join(SOURCES_PROJECTION))
    yield from _paginate(query, per_page=config.openalex.per_page, limit=limit)


def iter_topics(
    *,
    config: OpenAlexIndexConfig,
    filters: dict[str, Any] | None = None,
    limit: int | None = None,
) -> Iterator[dict[str, Any]]:
    _ensure_configured(config)
    query = Topics()
    if filters:
        query = query.filter(**filters)
    query = query.select(",".join(TOPICS_PROJECTION))
    yield from _paginate(query, per_page=config.openalex.per_page, limit=limit)


def iter_concepts(
    *,
    config: OpenAlexIndexConfig,
    filters: dict[str, Any] | None = None,
    limit: int | None = None,
) -> Iterator[dict[str, Any]]:
    _ensure_configured(config)
    query = Concepts()
    if filters:
        query = query.filter(**filters)
    query = query.select(",".join(CONCEPTS_PROJECTION))
    yield from _paginate(query, per_page=config.openalex.per_page, limit=limit)
