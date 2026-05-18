"""Text fetch stage: download the TEXT bundle's plaintext bitstream per item.

For each UUID in `raw/items/`, locate the bundle named `TEXT`, find a
`.txt` / `text/plain` bitstream inside, download it, and save verbatim to
`text/{uuid}.txt`. Skips items already on disk and items with no TEXT
bundle. We never touch PDFs.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

import httpx

from .config import EthzResearchCollectionIndexConfig
from .dspace import DSpaceClient
from .paths import raw_items_dir, text_dir

logger = logging.getLogger(__name__)

_TEXT_BUNDLE_NAME = "TEXT"


def _pick_text_bitstream(bitstreams: list) -> Optional[dict]:
    """Pick the first bitstream that looks like extracted plain text."""
    for bs in bitstreams:
        name = (bs.get("name") or "").lower()
        mime = (bs.get("metadata", {}).get("dc.format.mimetype", [{}]) or [{}])[0]
        mime_value = (mime.get("value") if isinstance(mime, dict) else "") or ""
        if name.endswith(".txt") or mime_value == "text/plain":
            return bs
    return bitstreams[0] if bitstreams else None


async def fetch_text_single(
    cfg: EthzResearchCollectionIndexConfig,
    *,
    uuid: str,
    refresh: bool = False,
) -> str:
    """Fetch the TEXT bundle for one UUID. Returns the same outcome string
    as the bulk runner. Spins up a one-off ``DSpaceClient`` per call; if you
    have many UUIDs to fetch use :func:`fetch_text` instead, which shares a
    client and a semaphore across the batch.
    """
    async with DSpaceClient(cfg.research_collection) as client:
        try:
            return await _fetch_one(client, uuid, text_dir(), refresh=refresh)
        except Exception:
            logger.exception("text-fetch failed for %s", uuid)
            return "error"


async def _fetch_one(
    client: DSpaceClient,
    uuid: str,
    out_dir: Path,
    *,
    refresh: bool = False,
) -> str:
    """Returns one of: 'written', 'skipped-existing', 'no-text-bundle',
    'no-bitstream', 'unauthorized', 'not-found', 'error'."""
    out_path = out_dir / f"{uuid}.txt"
    if out_path.exists() and not refresh:
        return "skipped-existing"
    try:
        bundles = await client.get_bundles(uuid)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return "not-found"
        if exc.response.status_code in (401, 403):
            return "unauthorized"
        raise

    text_bundle = next(
        (b for b in bundles if (b.get("name") or "").upper() == _TEXT_BUNDLE_NAME),
        None,
    )
    if text_bundle is None:
        return "no-text-bundle"

    bitstreams = await client.get_bitstreams(text_bundle["uuid"])
    bs = _pick_text_bitstream(bitstreams)
    if bs is None:
        return "no-bitstream"

    try:
        body = await client.get_bitstream_content(bs["uuid"])
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403):
            return "unauthorized"
        if exc.response.status_code == 404:
            return "not-found"
        raise

    text = body.decode("utf-8", errors="replace")
    out_path.write_text(text, encoding="utf-8")
    return "written"


async def fetch_text(
    cfg: EthzResearchCollectionIndexConfig,
    *,
    refresh: bool = False,
) -> dict:
    """Fetch text for every UUID found in raw/items/."""
    out_dir = text_dir()
    item_files = sorted(raw_items_dir().glob("*.json"))
    if not item_files:
        logger.warning("No items in raw/items/. Run `discover` first.")
        return {"items": 0}

    counts = {
        "written": 0,
        "skipped-existing": 0,
        "no-text-bundle": 0,
        "no-bitstream": 0,
        "unauthorized": 0,
        "not-found": 0,
        "error": 0,
    }

    async with DSpaceClient(cfg.research_collection) as client:
        sem = asyncio.Semaphore(cfg.research_collection.max_concurrency)

        async def _bounded(uuid: str) -> str:
            async with sem:
                try:
                    return await _fetch_one(client, uuid, out_dir, refresh=refresh)
                except Exception:
                    logger.exception("text-fetch failed for %s", uuid)
                    return "error"

        uuids = [p.stem for p in item_files]
        results = await asyncio.gather(*(_bounded(u) for u in uuids))

    for r in results:
        counts[r] = counts.get(r, 0) + 1

    logger.info("fetch_text: %s", json.dumps(counts))
    return {"items": len(uuids), **counts, "text_dir": str(out_dir)}


def run(cfg: EthzResearchCollectionIndexConfig, **kwargs) -> dict:
    return asyncio.run(fetch_text(cfg, **kwargs))
