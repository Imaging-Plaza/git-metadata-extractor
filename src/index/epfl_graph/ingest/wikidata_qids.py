"""Backfill ``wikidata_qid`` for ontology categories from Wikipedia page ids.

Each EPFL Graph category row already carries a ``wikipedia_page_id``. The
MediaWiki API exposes the corresponding Wikidata QID via
``prop=pageprops&ppprop=wikibase_item``. We batch up to 50 page ids per
request and persist the result on the ``categories`` row, so downstream
consumers (e.g. ontology curation) can stamp granular Wikidata IRIs onto
``pulse:DisciplineEnumeration`` without re-querying the network.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

import requests

if TYPE_CHECKING:
    from src.index.epfl_graph.config import EpflGraphIndexConfig
    from src.index.epfl_graph.storage.duckdb_store import EpflGraphStore

LOGGER = logging.getLogger(__name__)

WIKIPEDIA_API_URL = "https://en.wikipedia.org/w/api.php"
USER_AGENT = (
    "git-metadata-extractor/2.0 (+https://github.com/Imaging-Plaza/"
    "git-metadata-extractor) (concept_tagging discipline ingest)"
)
# pageprops accepts the full 50-page batch (no exlimit cap, unlike extracts).
MAX_PAGE_IDS_PER_REQUEST = 50
RATE_PER_SECOND = 5
DEFAULT_TIMEOUT = 30


def _fetch_qids_batch(
    page_ids: list[str],
    *,
    session: requests.Session,
    timeout: float,
) -> dict[str, str]:
    """Return ``{page_id_str: wikidata_qid}`` for the batch.

    Pages without a ``wikibase_item`` (i.e. no associated Wikidata entity) are
    silently omitted from the result.
    """

    if not page_ids:
        return {}
    response = session.get(
        WIKIPEDIA_API_URL,
        params={
            "action": "query",
            "format": "json",
            "prop": "pageprops",
            "ppprop": "wikibase_item",
            "pageids": "|".join(page_ids),
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    query = payload.get("query") if isinstance(payload, dict) else None
    pages = query.get("pages") if isinstance(query, dict) else None
    if not isinstance(pages, dict):
        return {}

    out: dict[str, str] = {}
    for page in pages.values():
        if not isinstance(page, dict):
            continue
        page_id = page.get("pageid")
        if not isinstance(page_id, int):
            continue
        wikibase_item = (page.get("pageprops") or {}).get("wikibase_item")
        if isinstance(wikibase_item, str) and wikibase_item.strip():
            out[str(page_id)] = wikibase_item.strip()
    return out


def fetch_wikidata_qids(
    config: EpflGraphIndexConfig,  # noqa: ARG001 — kept for symmetry with sister enrichers
    store: EpflGraphStore,
    *,
    limit: int | None = None,
    log_every: int = 200,
) -> int:
    """Fill in missing ``wikidata_qid`` columns. Returns rows updated."""

    pending = list(store.iter_categories_missing_wikidata_qid())
    if limit is not None:
        pending = pending[:limit]
    if not pending:
        LOGGER.info("epfl_graph: no categories missing wikidata_qid")
        return 0
    LOGGER.info(
        "epfl_graph: fetching wikidata qids for %d categories", len(pending),
    )

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    interval = 1.0 / max(1, RATE_PER_SECOND)
    last_call = 0.0
    updated = 0

    # Multiple categories can share the same wikipedia_page_id (e.g. a top-level
    # discipline and its `entities-in-*` / `topics-in-*` siblings often anchor
    # to the same Wikipedia article). Keep all of them and update each row when
    # the QID resolves.
    page_id_to_categories: dict[str, list[str]] = {}
    for row in pending:
        category_id = row.get("category_id")
        page_id = row.get("wikipedia_page_id")
        if not category_id:
            continue
        if not isinstance(page_id, (str, int)) or not str(page_id).strip():
            continue
        page_id_to_categories.setdefault(str(page_id).strip(), []).append(
            str(category_id),
        )

    page_ids = list(page_id_to_categories.keys())
    for index in range(0, len(page_ids), MAX_PAGE_IDS_PER_REQUEST):
        batch = page_ids[index : index + MAX_PAGE_IDS_PER_REQUEST]
        now = time.monotonic()
        wait = max(0.0, last_call + interval - now)
        if wait:
            time.sleep(wait)
        last_call = time.monotonic()

        try:
            qids = _fetch_qids_batch(
                batch, session=session, timeout=DEFAULT_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(
                "epfl_graph: wikidata qid batch failed at offset %d: %s",
                index, exc,
            )
            continue

        for page_id, qid in qids.items():
            for category_id in page_id_to_categories.get(page_id, []):
                try:
                    store.update_wikidata_qid(category_id, qid)
                    updated += 1
                except Exception as exc:  # noqa: BLE001
                    LOGGER.warning(
                        "epfl_graph: failed to upsert wikidata_qid for %s: %s",
                        category_id, exc,
                    )
        if updated and updated % log_every == 0:
            LOGGER.info(
                "epfl_graph: updated %d / %d wikidata qids",
                updated, len(page_ids),
            )

    LOGGER.info(
        "epfl_graph: wikidata qid fetch complete — %d updated", updated,
    )
    return updated
