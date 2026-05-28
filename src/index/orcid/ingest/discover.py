"""Build a seed list of ORCID identifiers for the configured scope.

Two sources, mixable via `discovery.source`:

- **openalex**: read the authors table of a previously-built OpenAlex
  DuckDB. Most reliable since OpenAlex's scope filter has already
  excluded non-EPFL / non-Swiss authors.
- **orcid_search**: ORCID's `expanded-search` keyed by affiliation
  aliases. Catches researchers who don't appear in OpenAlex (e.g.,
  students, PIs without recent works).

Seeds are deduped and persisted to the `seeds` DuckDB table — re-runs
add new seeds without dropping old ones.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Iterator

import duckdb
import requests

from src.index.orcid.ingest.orcid_client import build_orcid_provider

if TYPE_CHECKING:
    from src.index.orcid.config import OrcidIndexConfig
    from src.index.orcid.storage.duckdb_store import OrcidDuckDBStore

LOGGER = logging.getLogger(__name__)

ORCID_RE = re.compile(r"\b(\d{4}-\d{4}-\d{4}-\d{3}[\dX])\b")
# ORCID's expanded-search rejects start>=10000 with HTTP 400. Stop before then.
ORCID_EXPANDED_SEARCH_DEEP_LIMIT = 10000
HTTP_BAD_REQUEST = 400


def discover_seeds(
    *,
    config: OrcidIndexConfig,
    store: OrcidDuckDBStore,
    source: str | None = None,
) -> dict[str, int]:
    """Run the configured discovery sources and persist seeds."""
    chosen = source or config.discovery.source
    summary: dict[str, int] = {"openalex": 0, "orcid_search": 0}
    if chosen in {"openalex", "both"}:
        summary["openalex"] = _seed_from_openalex(config=config, store=store)
    if chosen in {"orcid_search", "both"}:
        summary["orcid_search"] = _seed_from_orcid_search(config=config, store=store)
    if chosen not in {"openalex", "orcid_search", "both"}:
        message = (
            f"Unknown discovery.source: {chosen!r}. "
            "Expected one of: openalex, orcid_search, both"
        )
        raise ValueError(message)
    return summary


def _seed_from_openalex(
    *,
    config: OrcidIndexConfig,
    store: OrcidDuckDBStore,
) -> int:
    db_path = Path(config.discovery.openalex_db)
    if not db_path.exists():
        LOGGER.warning(
            "openalex DuckDB not found at %s — skipping openalex discovery",
            db_path,
        )
        return 0
    count = 0
    for orcid_id in _iter_openalex_orcids(db_path):
        store.upsert_seed(
            orcid_id=orcid_id,
            discovered_via="openalex",
            hint=str(db_path),
        )
        count += 1
    LOGGER.info("openalex discovery: %d ORCIDs seeded from %s", count, db_path)
    return count


def _iter_openalex_orcids(db_path: Path) -> Iterator[str]:
    """Yield deduped, normalized ORCID IDs from the openalex authors table."""
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        cur = conn.execute(
            "SELECT DISTINCT orcid FROM authors WHERE orcid IS NOT NULL AND orcid != ''",
        )
        seen: set[str] = set()
        while True:
            row = cur.fetchone()
            if row is None:
                return
            normalized = _normalize_orcid(row[0])
            if normalized is None or normalized in seen:
                continue
            seen.add(normalized)
            yield normalized
    finally:
        conn.close()


def _normalize_orcid(value: str) -> str | None:
    if not value:
        return None
    match = ORCID_RE.search(value)
    if match:
        return match.group(1).upper()
    return None


def _seed_from_orcid_search(
    *,
    config: OrcidIndexConfig,
    store: OrcidDuckDBStore,
) -> int:
    provider = build_orcid_provider(config)
    aliases = [a for a in config.scope.affiliation_aliases if a.strip()]
    if not aliases:
        LOGGER.info("orcid_search discovery skipped: no affiliation_aliases configured")
        return 0
    count = 0
    for alias in aliases:
        for orcid_id in _paged_search(provider, alias, config=config):
            store.upsert_seed(
                orcid_id=orcid_id,
                discovered_via="orcid_search",
                hint=alias,
            )
            count += 1
    LOGGER.info("orcid_search discovery: %d seeds across %d aliases", count, len(aliases))
    return count


def _paged_search(
    provider: object,
    alias: str,
    *,
    config: OrcidIndexConfig,
) -> Iterable[str]:
    rows_per_page = config.orcid.search_max_rows
    start = 0
    query = f'affiliation-org-name:"{alias}"'
    while True:
        # ORCID's expanded-search caps deep pagination at start=10000 and
        # returns HTTP 400 above that. The start=10000 call itself succeeds
        # and yields up to 200 hits; the next page at start=10200 fails.
        # Stop proactively right after the last accepted page; the HTTP 400
        # catch below is the safety net if the threshold drifts.
        if start > ORCID_EXPANDED_SEARCH_DEEP_LIMIT:
            LOGGER.info(
                "alias %r: stopping at ORCID deep-pagination limit (start=%d); "
                "may be missing rare matches beyond the first %d hits",
                alias,
                start,
                ORCID_EXPANDED_SEARCH_DEEP_LIMIT,
            )
            return
        try:
            # Provider type is duck-typed here; signature comes from RealORCIDProvider.
            hits = provider.search_persons(  # type: ignore[attr-defined]
                query,
                rows=rows_per_page,
                start=start,
            )
        except requests.exceptions.HTTPError as exc:
            response = exc.response
            if response is not None and response.status_code == HTTP_BAD_REQUEST:
                LOGGER.info(
                    "alias %r: ORCID expanded-search HTTP 400 at start=%d "
                    "(treating as end of pagination)",
                    alias,
                    start,
                )
                return
            raise
        if not hits:
            return
        seen_in_page = 0
        for hit in hits:
            orcid_id = hit.get("orcid_id") if isinstance(hit, dict) else hit["orcid_id"]
            if not orcid_id:
                continue
            yield orcid_id.upper()
            seen_in_page += 1
        # Last page: signal exit.
        if seen_in_page < rows_per_page:
            return
        start += rows_per_page
