"""Fetch related stage: GET /core/items/{uuid} for each Person/Org UUID.

Reads `persons.txt` and `organizations.txt` (produced by `extract_relations`),
fetches each unique UUID once, and persists the raw JSON to
`raw/persons/{uuid}.json` or `raw/organizations/{uuid}.json`. Skips
already-fetched files unless `refresh=True`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Iterable, Optional

import httpx

from .config import EthzResearchCollectionIndexConfig
from .dspace import DSpaceClient
from .extract_relations import load_set
from .paths import (
    organizations_set_path,
    persons_set_path,
    raw_organizations_dir,
    raw_persons_dir,
)

logger = logging.getLogger(__name__)


async def fetch_related_single(
    cfg: EthzResearchCollectionIndexConfig,
    *,
    uuid: str,
    kind: str,
    refresh: bool = False,
) -> str:
    """Fetch one Person or OrgUnit by UUID. ``kind`` ∈ {``"person"``, ``"org"``}.

    Returns the same outcome string as the bulk fetcher
    (``written | skipped-existing | unauthorized | not-found | error``).
    """
    if kind not in {"person", "org"}:
        return "error"
    out_dir = raw_persons_dir() if kind == "person" else raw_organizations_dir()
    async with DSpaceClient(cfg.research_collection) as client:
        try:
            return await _fetch_one(client, uuid, out_dir, refresh=refresh)
        except Exception:
            logger.exception("fetch-related failed for %s (%s)", uuid, kind)
            return "error"


async def _fetch_one(
    client: DSpaceClient,
    uuid: str,
    out_dir: Path,
    *,
    refresh: bool = False,
) -> str:
    out_path = out_dir / f"{uuid}.json"
    if out_path.exists() and not refresh:
        return "skipped-existing"
    try:
        item = await client.get_item(uuid)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403):
            return "unauthorized"
        if exc.response.status_code == 404:
            return "not-found"
        raise
    if item is None:
        return "not-found"
    out_path.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
    return "written"


async def _fetch_set(
    cfg: EthzResearchCollectionIndexConfig,
    uuids: Iterable[str],
    out_dir: Path,
    *,
    refresh: bool,
) -> dict:
    counts = {"written": 0, "skipped-existing": 0, "unauthorized": 0,
              "not-found": 0, "error": 0}
    uuid_list = list(uuids)
    if not uuid_list:
        return counts

    async with DSpaceClient(cfg.research_collection) as client:
        sem = asyncio.Semaphore(cfg.research_collection.max_concurrency)

        async def _bounded(u: str) -> str:
            async with sem:
                try:
                    return await _fetch_one(client, u, out_dir, refresh=refresh)
                except Exception:
                    logger.exception("fetch-related failed for %s", u)
                    return "error"

        results = await asyncio.gather(*(_bounded(u) for u in uuid_list))
    for r in results:
        counts[r] = counts.get(r, 0) + 1
    return counts


async def fetch_related(
    cfg: EthzResearchCollectionIndexConfig,
    *,
    kind: str = "all",
    refresh: bool = False,
) -> dict:
    """`kind` ∈ {'person', 'org', 'all'}."""
    summary: dict = {"kind": kind}
    if kind in ("person", "all"):
        persons = load_set(persons_set_path())
        summary["persons"] = await _fetch_set(
            cfg, persons, raw_persons_dir(), refresh=refresh,
        )
    if kind in ("org", "all"):
        orgs = load_set(organizations_set_path())
        summary["organizations"] = await _fetch_set(
            cfg, orgs, raw_organizations_dir(), refresh=refresh,
        )
    logger.info("fetch_related: %s", json.dumps(summary))
    return summary


def run(cfg: EthzResearchCollectionIndexConfig, *, kind: str = "all", refresh: bool = False) -> dict:
    return asyncio.run(fetch_related(cfg, kind=kind, refresh=refresh))
