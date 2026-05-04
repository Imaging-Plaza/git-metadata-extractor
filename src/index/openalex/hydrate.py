"""Hydrate canonical OpenAlex records from a stream of :class:`Seed`s.

Implements :class:`src.index._federated.protocols.IndexHydrator`.

Accepted seed types
-------------------

- ``doi`` — fetch ``GET /works/doi:<DOI>``, upsert into ``works``,
  link authors / institutions. If ``hint.affiliation_ror`` is set,
  also stamp ``work_institutions`` with that ROR's OpenAlex institution
  ID (resolved on first use, cached).
- ``openalex_work`` — like ``doi`` but by direct OpenAlex Work ID.
  Special mode: ``hint.refs_only=True`` runs the references-backfill
  fast path — bulk-fetches up to 100 IDs per request with
  ``select=id,referenced_works``, populates ``work_references`` only,
  no work upsert.
- ``openalex_author`` — fetch ``GET /authors/<ID>``, upsert into ``authors``.

All upserts are idempotent (ON CONFLICT DO UPDATE/NOTHING). Re-running
with the same seeds is safe.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Iterable

import requests

from src.index._federated.protocols import HydrationSummary, IndexHydrator, Seed
from src.index.openalex.ingest.authors import _project_author
from src.index.openalex.ingest.works import persist_work
from src.index.openalex.storage.duckdb_store import DuckDBStore

LOGGER = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (gme-openalex-hydrate)"
WORK_BATCH = 100  # OpenAlex max IDs per filter
AUTHOR_BATCH = 100
SLEEP = 0.15


def _mailto_params() -> dict:
    m = os.getenv("OPENALEX_MAILTO", "")
    return {"mailto": m} if m else {}


def _http_get(url: str, params: dict | None = None, timeout: int = 60) -> requests.Response:
    return requests.get(
        url, params=params, headers={"User-Agent": USER_AGENT}, timeout=timeout,
    )


def _short_id(full: str) -> str:
    return full.rsplit("/", 1)[-1]


def _ror_to_openalex_institution(ror: str, cache: dict[str, str]) -> str | None:
    """Resolve a ROR URL to the OpenAlex institution ID (cached)."""
    if ror in cache:
        return cache[ror]
    short = ror.rstrip("/").rsplit("/", 1)[-1]
    try:
        r = _http_get(
            f"https://api.openalex.org/institutions/ror:{short}",
            params=_mailto_params(),
        )
        r.raise_for_status()
        oid = r.json().get("id")
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("ROR %s → OpenAlex institution lookup failed: %s", ror, exc)
        oid = None
    cache[ror] = oid or ""
    return oid


def _hydrate_dois(
    seeds: list[Seed],
    *,
    store: DuckDBStore,
    only_unfetched: bool,
    summary: HydrationSummary,
    ror_cache: dict[str, str],
) -> None:
    """Fetch each DOI individually via /works/doi:X and upsert."""
    cur = store.connect()
    if only_unfetched:
        existing = {
            row[0]
            for row in cur.execute(
                "SELECT LOWER(REPLACE(doi,'https://doi.org/','')) "
                "FROM works WHERE doi IS NOT NULL",
            ).fetchall()
        }
    else:
        existing = set()

    for seed in seeds:
        doi = seed.id
        normalised = doi.lower().replace("https://doi.org/", "").rstrip("/")
        if only_unfetched and normalised in existing:
            summary.skipped_existing += 1
            _maybe_stamp(seed, store, ror_cache, summary)
            continue
        try:
            r = _http_get(
                f"https://api.openalex.org/works/doi:{normalised}",
                params=_mailto_params(),
            )
            if r.status_code == 404:
                summary.errors += 1
                continue
            r.raise_for_status()
            item = r.json()
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("DOI fetch failed for %s: %s", doi, exc)
            summary.errors += 1
            time.sleep(0.5)
            continue
        wid = persist_work(store, item)
        if wid:
            summary.fetched += 1
            summary.in_scope += 1
            _stamp(wid, seed, store, ror_cache, summary)
        time.sleep(SLEEP)


def _hydrate_works_full(
    seeds: list[Seed],
    *,
    store: DuckDBStore,
    only_unfetched: bool,
    summary: HydrationSummary,
    ror_cache: dict[str, str],
) -> None:
    """Fetch + upsert OpenAlex Works one by one (refs_only=False)."""
    cur = store.connect()
    existing = (
        {row[0] for row in cur.execute("SELECT openalex_id FROM works").fetchall()}
        if only_unfetched else set()
    )
    for seed in seeds:
        wid = seed.id
        if only_unfetched and wid in existing:
            summary.skipped_existing += 1
            _maybe_stamp(seed, store, ror_cache, summary)
            continue
        short = _short_id(wid)
        try:
            r = _http_get(
                f"https://api.openalex.org/works/{short}",
                params=_mailto_params(),
            )
            if r.status_code == 404:
                summary.errors += 1
                continue
            r.raise_for_status()
            item = r.json()
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("work fetch failed for %s: %s", wid, exc)
            summary.errors += 1
            time.sleep(0.5)
            continue
        new_id = persist_work(store, item)
        if new_id:
            summary.fetched += 1
            summary.in_scope += 1
            _stamp(new_id, seed, store, ror_cache, summary)
        time.sleep(SLEEP)


def _hydrate_works_refs_only(
    seeds: list[Seed],
    *,
    store: DuckDBStore,
    summary: HydrationSummary,
) -> None:
    """Bulk references backfill — 100 IDs / request, only updates work_references.

    Note: ``only_unfetched`` is implicit — discoverer ``from-references``
    already filters out works whose refs are populated. We don't double-check
    here.
    """
    cur = store.connect()
    refs_inserted_total = 0
    for i in range(0, len(seeds), WORK_BATCH):
        batch = seeds[i : i + WORK_BATCH]
        ids = [_short_id(s.id) for s in batch]
        params = {
            "filter": "ids.openalex:" + "|".join(ids),
            "select": "id,referenced_works",
            "per-page": str(WORK_BATCH),
            **_mailto_params(),
        }
        try:
            r = _http_get("https://api.openalex.org/works", params=params)
            r.raise_for_status()
            results = r.json().get("results", []) or []
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(
                "refs-only batch %d-%d failed: %s", i, i + len(batch), exc,
            )
            summary.errors += len(batch)
            time.sleep(2)
            continue
        rows: list[tuple[str, str, int]] = []
        for item in results:
            wid = item.get("id")
            if not wid:
                continue
            refs = item.get("referenced_works") or []
            summary.fetched += 1
            if refs:
                summary.in_scope += 1
            else:
                summary.out_of_scope += 1  # work has no references
            for pos, cited in enumerate(refs):
                if cited:
                    rows.append((wid, cited, pos))
        if rows:
            cur.executemany(
                "INSERT OR IGNORE INTO work_references "
                "(citing_work_id, cited_work_id, position) VALUES (?, ?, ?)",
                rows,
            )
            refs_inserted_total += len(rows)
        time.sleep(SLEEP)
    summary.extras["refs_inserted"] = refs_inserted_total


def _hydrate_authors(
    seeds: list[Seed],
    *,
    store: DuckDBStore,
    only_unfetched: bool,
    summary: HydrationSummary,
) -> None:
    """Fetch + upsert OpenAlex Authors one by one."""
    cur = store.connect()
    existing = (
        {row[0] for row in cur.execute("SELECT openalex_id FROM authors").fetchall()}
        if only_unfetched else set()
    )
    for seed in seeds:
        aid = seed.id
        if only_unfetched and aid in existing:
            summary.skipped_existing += 1
            continue
        short = _short_id(aid)
        try:
            r = _http_get(
                f"https://api.openalex.org/authors/{short}",
                params=_mailto_params(),
            )
            if r.status_code == 404:
                summary.errors += 1
                continue
            r.raise_for_status()
            item = r.json()
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("author fetch failed for %s: %s", aid, exc)
            summary.errors += 1
            time.sleep(0.5)
            continue
        row = _project_author(item)
        store.upsert_author(row, raw=item)
        summary.fetched += 1
        summary.in_scope += 1
        time.sleep(SLEEP)


def _stamp(
    work_id: str, seed: Seed, store: DuckDBStore,
    ror_cache: dict[str, str], summary: HydrationSummary,
) -> None:
    """If seed.hint specifies an affiliation_ror, stamp work_institutions."""
    if not seed.hint:
        return
    ror = seed.hint.get("affiliation_ror")
    if not ror:
        return
    inst_id = _ror_to_openalex_institution(ror, ror_cache)
    if not inst_id:
        return
    store.upsert_work_institutions(work_id, {inst_id})
    summary.extras["stamped_affiliations"] = (
        summary.extras.get("stamped_affiliations", 0) + 1
    )


def _maybe_stamp(
    seed: Seed, store: DuckDBStore,
    ror_cache: dict[str, str], summary: HydrationSummary,
) -> None:
    """Stamp affiliation for a seed whose work is already in DB."""
    if not seed.hint or not seed.hint.get("affiliation_ror"):
        return
    cur = store.connect()
    if seed.seed_type == "doi":
        normalised = seed.id.lower().replace("https://doi.org/", "").rstrip("/")
        row = cur.execute(
            "SELECT openalex_id FROM works "
            "WHERE LOWER(REPLACE(doi,'https://doi.org/','')) = ?",
            [normalised],
        ).fetchone()
    else:
        row = cur.execute(
            "SELECT openalex_id FROM works WHERE openalex_id = ?", [seed.id],
        ).fetchone()
    if not row:
        return
    _stamp(row[0], seed, store, ror_cache, summary)


class OpenAlexHydrator:
    """Hydrator for the OpenAlex index. See module docstring."""

    name = "openalex"
    accepted_seed_types = ("doi", "openalex_work", "openalex_author")

    def hydrate(
        self,
        seeds: Iterable[Seed],
        *,
        only_unfetched: bool = True,
    ) -> HydrationSummary:
        store = DuckDBStore.open()
        summary = HydrationSummary()
        ror_cache: dict[str, str] = {}

        # Bucket by seed_type + refs_only flag.
        dois: list[Seed] = []
        works_full: list[Seed] = []
        works_refs_only: list[Seed] = []
        authors: list[Seed] = []
        for s in seeds:
            if s.seed_type == "doi":
                dois.append(s)
            elif s.seed_type == "openalex_work":
                if s.hint and s.hint.get("refs_only"):
                    works_refs_only.append(s)
                else:
                    works_full.append(s)
            elif s.seed_type == "openalex_author":
                authors.append(s)
            # else: silently ignored (federated dispatch will route elsewhere)

        if dois:
            _hydrate_dois(
                dois, store=store, only_unfetched=only_unfetched,
                summary=summary, ror_cache=ror_cache,
            )
        if works_full:
            _hydrate_works_full(
                works_full, store=store, only_unfetched=only_unfetched,
                summary=summary, ror_cache=ror_cache,
            )
        if works_refs_only:
            _hydrate_works_refs_only(
                works_refs_only, store=store, summary=summary,
            )
        if authors:
            _hydrate_authors(
                authors, store=store, only_unfetched=only_unfetched,
                summary=summary,
            )
        return summary


HYDRATOR = OpenAlexHydrator()


__all__ = ["HYDRATOR", "OpenAlexHydrator"]
