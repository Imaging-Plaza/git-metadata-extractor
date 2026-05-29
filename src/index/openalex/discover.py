"""Seed discovery for the OpenAlex index.

Implements :class:`src.index._federated.protocols.IndexDiscoverer`.
Each ``--source`` produces a stream of :class:`Seed`s consumable by
:class:`src.index.openalex.hydrate.OpenAlexHydrator`.

Sources
-------

- ``from-search`` — OpenAlex ``/works`` semantic search. Emits
  ``seed_type="openalex_work"`` seeds. Required opt: ``query``.
- ``from-references`` — for every work in the local DB whose
  ``referenced_works`` are not yet populated in ``work_references``,
  emit one ``openalex_work`` seed with ``hint={"refs_only": True}``.
  Drives the citation-graph backfill.
- ``datascience-ch`` — scrape the SDSC publications page
  (``https://datascience.ch/publications``, paginated via ``?<hash>_page=N``)
  and emit ``seed_type="doi"`` seeds. Required opt: none. Optional:
  ``url`` (override default), ``pages`` (limit, useful for testing),
  ``affiliation_ror`` (stamped onto each seed's hint).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Iterator

import requests

from src.index._federated.protocols import IndexDiscoverer, Seed
from src.index.openalex.storage.duckdb_store import OpenAlexStore

LOGGER = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (gme-openalex-discover)"
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>)]+")

DATASCIENCE_CH_URL = "https://datascience.ch/publications"
DATASCIENCE_CH_HASH = "7a5b1ea3"  # webflow per-section pagination prefix
DATASCIENCE_CH_TOTAL_PAGES = 32
SDSC_ROR = "https://ror.org/02hdt9m26"


def _http_get(url: str, params: dict | None = None, timeout: int = 30) -> requests.Response:
    headers = {"User-Agent": USER_AGENT}
    return requests.get(url, params=params, headers=headers, timeout=timeout)


def _from_search(*, query: str, limit: int = 200, **_unused: Any) -> Iterator[Seed]:
    """OpenAlex /works search → openalex_work seeds.

    Uses the public REST endpoint directly to avoid a hard pyalex
    dependency in the discoverer (kept light for cheap import).
    """
    if not query:
        message = "from-search requires --opt query='...'"
        raise ValueError(message)

    mailto = os.getenv("OPENALEX_MAILTO", "")
    page = 1
    seen = 0
    while seen < limit:
        params = {
            "search": query,
            "select": "id,doi,title",
            "per-page": str(min(200, limit - seen)),
            "page": str(page),
        }
        if mailto:
            params["mailto"] = mailto
        r = _http_get("https://api.openalex.org/works", params=params)
        r.raise_for_status()
        data = r.json()
        results = data.get("results", []) or []
        if not results:
            break
        for item in results:
            wid = item.get("id")
            if not wid:
                continue
            yield Seed(
                id=wid,
                seed_type="openalex_work",
                source="from-search",
                hint={"query": query, "title": item.get("title"), "doi": item.get("doi")},
            )
            seen += 1
            if seen >= limit:
                break
        if len(results) < int(params["per-page"]):
            break
        page += 1


def _from_references(**_unused: Any) -> Iterator[Seed]:
    """Works in DB without populated `referenced_works` → seeds for backfill.

    Emits one ``openalex_work`` seed per such work, with
    ``hint={"refs_only": True}`` so the hydrator knows to populate only
    ``work_references`` (cheap select) rather than re-upserting the work.
    """
    store = OpenAlexStore.open()
    cur = store.connect()
    rows = cur.execute("""
      SELECT w.openalex_id FROM works w
      LEFT JOIN (
        SELECT DISTINCT citing_work_id FROM work_references
      ) r ON r.citing_work_id = w.openalex_id
      WHERE r.citing_work_id IS NULL
      ORDER BY w.openalex_id
    """).fetchall()
    LOGGER.info("from-references: %d works without populated references", len(rows))
    for (wid,) in rows:
        yield Seed(
            id=wid,
            seed_type="openalex_work",
            source="from-references",
            hint={"refs_only": True},
        )


def _from_datascience_ch(
    *, url: str = DATASCIENCE_CH_URL, pages: int = DATASCIENCE_CH_TOTAL_PAGES,
    hash_: str = DATASCIENCE_CH_HASH, affiliation_ror: str = SDSC_ROR,
    **_unused: Any,
) -> Iterator[Seed]:
    """Scrape the SDSC publications page (32 paginated lists) → DOI seeds.

    Each emitted seed carries ``hint.affiliation_ror`` so the hydrator
    can stamp the SDSC institution link on hydrated works (regardless of
    whether OpenAlex itself attributes them to SDSC).
    """
    seen: set[str] = set()
    for p in range(1, pages + 1):
        page_url = url if p == 1 else f"{url}?{hash_}_page={p}"
        try:
            r = _http_get(page_url)
            r.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("datascience-ch: page %d failed: %s", p, exc)
            continue
        page_dois: set[str] = set()
        for raw in DOI_RE.findall(r.text):
            d = raw.rstrip(".,;:)")
            if d.lower().startswith("10.5281/zenodo."):
                continue  # Zenodo datasets don't index in OpenAlex
            page_dois.add(d)
        new = page_dois - seen
        seen |= new
        for d in new:
            yield Seed(
                id=d,
                seed_type="doi",
                source="datascience-ch",
                hint={"affiliation_ror": affiliation_ror, "scrape_url": page_url},
            )
    LOGGER.info("datascience-ch: emitted %d DOI seeds", len(seen))


_DISPATCH = {
    "from-search": _from_search,
    "from-references": _from_references,
    "datascience-ch": _from_datascience_ch,
}


class OpenAlexDiscoverer:
    """Discoverer for the OpenAlex index. See module docstring for sources."""

    name = "openalex"
    accepted_sources = tuple(_DISPATCH.keys())

    def discover(self, source: str, **opts: Any) -> Iterator[Seed]:
        if source not in _DISPATCH:
            message = (
                f"OpenAlex: unknown source {source!r}. "
                f"Accepted: {sorted(self.accepted_sources)}"
            )
            raise ValueError(message)
        return _DISPATCH[source](**opts)


# Public symbol: register at import time from `src/index/openalex/_federated.py`.
DISCOVERER = OpenAlexDiscoverer()


__all__ = ["DISCOVERER", "OpenAlexDiscoverer"]
