"""Discover stage: paginate Solr `fulltext:` queries, persist raw item JSON.

Output: one JSON file per matched item under `raw/items/{uuid}.json`, plus
`discover_state.json` with per-term cursor for resumability. Same UUID
matched by multiple terms is stored once (the file write is idempotent).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
from typing import List, Optional

from .config import InfoscienceIndexConfig
from .dspace import DSpaceClient
from .models import DiscoverState
from .paths import discover_state_path, raw_items_dir

logger = logging.getLogger(__name__)


def _load_state() -> DiscoverState:
    path = discover_state_path()
    if not path.exists():
        return DiscoverState()
    return DiscoverState(**json.loads(path.read_text(encoding="utf-8")))


def _save_state(state: DiscoverState) -> None:
    discover_state_path().write_text(
        state.model_dump_json(indent=2), encoding="utf-8",
    )


def _write_item(item: dict) -> bool:
    """Persist one item; returns True if newly written."""
    uuid = item.get("uuid")
    if not uuid:
        return False
    path = raw_items_dir() / f"{uuid}.json"
    if path.exists():
        return False
    path.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


async def discover(
    cfg: InfoscienceIndexConfig,
    *,
    terms: Optional[List[str]] = None,
    limit: Optional[int] = None,
) -> dict:
    """Run the discover stage. Returns a summary dict."""
    target_terms = list(terms or cfg.filter.terms)
    if not target_terms:
        msg = "No filter terms configured."
        raise ValueError(msg)

    state = _load_state()
    total_new = 0
    total_seen = 0

    async with DSpaceClient(cfg.infoscience) as client:
        for term in target_terms:
            if state.completed.get(term):
                logger.info("Skipping completed term: %s", term)
                continue
            start_page = state.per_term_cursor.get(term, 0)
            new_for_term = 0
            seen_for_term = 0
            logger.info("Discovering term=%s starting at page=%d", term, start_page)
            page = start_page
            try:
                async for item in client.iter_discover_fulltext(
                    term,
                    size=cfg.infoscience.page_size,
                    start_page=start_page,
                ):
                    seen_for_term += 1
                    total_seen += 1
                    if _write_item(item):
                        new_for_term += 1
                        total_new += 1
                    if limit is not None and total_new >= limit:
                        break
                    # Cursor in pages; updated whenever a page boundary passes.
                    new_page = start_page + (seen_for_term // cfg.infoscience.page_size)
                    if new_page != page:
                        page = new_page
                        state.per_term_cursor[term] = page
                        _save_state(state)
            except Exception as exc:
                logger.exception("Discover failed for term=%s: %s", term, exc)
                state.per_term_cursor[term] = page
                _save_state(state)
                raise
            else:
                state.per_term_cursor[term] = page
                state.per_term_total[term] = state.per_term_total.get(term, 0) + new_for_term
                if limit is None:
                    state.completed[term] = True
                logger.info(
                    "term=%s seen=%d new=%d",
                    term, seen_for_term, new_for_term,
                )

            if limit is not None and total_new >= limit:
                logger.info("Hit --limit %d, stopping.", limit)
                break

    state.last_run_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    _save_state(state)
    return {
        "terms": target_terms,
        "items_seen": total_seen,
        "items_new": total_new,
        "raw_dir": str(raw_items_dir()),
    }


def run(cfg: InfoscienceIndexConfig, **kwargs) -> dict:
    """Sync wrapper for the CLI."""
    return asyncio.run(discover(cfg, **kwargs))
