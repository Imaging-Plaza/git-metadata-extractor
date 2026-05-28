"""Fetch canonical Wikipedia lead-section extracts for ontology categories.

Each EPFL Graph category's ``info.id`` is a real Wikipedia page ID. We
hit MediaWiki's TextExtracts extension to pull the lead-section plain
text and persist it on the ``categories`` row. The fold-in into the
embedding text happens here too: we rebuild ``embedding_text`` from
``name + wikipedia_extract + anchor concept names`` so a follow-up
``embed`` pass picks up the richer signal.

Batching: the API accepts up to 50 ``pageids`` per request and is
generous with rate limits when given a real ``User-Agent``.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

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
# Wikipedia's TextExtracts module silently caps `exlimit=max` at 20 pages per
# request when `explaintext` / `exintro` are set. Batching more than 20 means
# the tail of each batch comes back with no `extract` field. Stay at 20 so
# every queued page actually gets a lead-section extract.
MAX_PAGE_IDS_PER_REQUEST = 20
RATE_PER_SECOND = 5
DEFAULT_TIMEOUT = 30
MAX_EXTRACT_CHARS_FOR_EMBED = 1200  # cap each extract for embedding text


def _fetch_extracts_batch_by_page_id(
    page_ids: list[str], *, session: requests.Session, timeout: float,
) -> dict[str, str]:
    """Page-id-keyed extract fetch. Returns ``{page_id_str: extract}``.

    Querying by pageid is deterministic — no title normalization, no redirect
    chasing — and each EPFL Graph category row already carries the canonical
    Wikipedia page id. Wikipedia returns the ``pageid`` integer per page in
    the response; we re-stringify it to match our input keys.
    """
    if not page_ids:
        return {}
    response = session.get(
        WIKIPEDIA_API_URL,
        params={
            "action": "query",
            "format": "json",
            "prop": "extracts",
            "exintro": "true",
            "explaintext": "true",
            "exlimit": "max",
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
        extract = page.get("extract")
        if not isinstance(extract, str) or not extract.strip():
            continue
        if not isinstance(page_id, int):
            continue
        out[str(page_id)] = extract.strip()
    return out


def fetch_wikipedia_extracts(  # noqa: C901
    config: EpflGraphIndexConfig,  # noqa: ARG001 — keep symmetric with rebuild_*
    store: EpflGraphStore,
    *,
    limit: int | None = None,
    log_every: int = 200,
) -> int:
    """Fill in missing ``wikipedia_extract`` columns. Returns rows updated."""
    pending = list(store.iter_categories_missing_extract())
    if limit is not None:
        pending = pending[:limit]
    if not pending:
        LOGGER.info("epfl_graph: no categories missing wikipedia_extract")
        return 0
    LOGGER.info(
        "epfl_graph: fetching wikipedia extracts for %d categories", len(pending),
    )

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    interval = 1.0 / max(1, RATE_PER_SECOND)
    last_call = 0.0
    updated = 0

    # Query by Wikipedia page id (stored in `categories.wikipedia_page_id`)
    # rather than title. Each row already carries the canonical pageid; using
    # it skips title-normalization and redirect-chasing entirely. Rows with a
    # missing pageid fall back to title-based lookup so we still cover them.
    page_id_to_category: dict[str, str] = {}
    title_only_rows: list[dict[str, Any]] = []
    for row in pending:
        category_id = row.get("category_id")
        if not category_id:
            continue
        page_id = row.get("wikipedia_page_id")
        if isinstance(page_id, (str, int)) and str(page_id).strip():
            page_id_to_category.setdefault(str(page_id).strip(), str(category_id))
        else:
            title_only_rows.append(row)

    page_ids = list(page_id_to_category.keys())
    for index in range(0, len(page_ids), MAX_PAGE_IDS_PER_REQUEST):
        batch = page_ids[index : index + MAX_PAGE_IDS_PER_REQUEST]
        now = time.monotonic()
        wait = max(0.0, last_call + interval - now)
        if wait:
            time.sleep(wait)
        last_call = time.monotonic()

        try:
            extracts = _fetch_extracts_batch_by_page_id(
                batch, session=session, timeout=DEFAULT_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(
                "epfl_graph: wikipedia batch failed at offset %d: %s", index, exc,
            )
            continue

        for page_id, extract in extracts.items():
            category_id = page_id_to_category.get(page_id)
            if not category_id:
                continue
            try:
                store.update_wikipedia_extract(category_id, extract)
                updated += 1
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning(
                    "epfl_graph: failed to upsert extract for %s: %s",
                    category_id, exc,
                )
        if updated and updated % log_every == 0:
            LOGGER.info(
                "epfl_graph: updated %d / %d wikipedia extracts",
                updated, len(page_ids),
            )

    if title_only_rows:
        LOGGER.info(
            "epfl_graph: %d categories without wikipedia_page_id — skipping",
            len(title_only_rows),
        )

    LOGGER.info(
        "epfl_graph: wikipedia extract fetch complete — %d updated", updated,
    )
    return updated


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    cutoff = text.rfind(" ", 0, max_chars)
    return text[: cutoff if cutoff > max_chars * 0.6 else max_chars].rstrip() + "…"


def build_embedding_text(
    *,
    name: str | None,
    wikipedia_extract: str | None,
    anchor_concept_names: list[str],
    max_extract_chars: int = MAX_EXTRACT_CHARS_FOR_EMBED,
) -> str:
    pieces: list[str] = []
    if name:
        pieces.append(name)
    if isinstance(wikipedia_extract, str) and wikipedia_extract.strip():
        pieces.append(_truncate(wikipedia_extract.strip(), max_extract_chars))
    if anchor_concept_names:
        pieces.append(
            "Anchor concepts: " + ", ".join(anchor_concept_names),
        )
    return ". ".join(pieces).strip()


def rebuild_embedding_texts(
    config: EpflGraphIndexConfig, store: EpflGraphStore,
) -> int:
    """Recompute ``embedding_text`` for every category from current data.

    Run this after :func:`fetch_wikipedia_extracts` so the new extracts
    feed into the next ``embed`` pass.
    """
    anchor_count = config.graphai.anchor_concepts_per_category
    rebuilt = 0
    for row in store.iter_categories_for_extract_refresh():
        category_id = row["category_id"]
        anchors = store.fetch_anchor_concept_names(category_id, anchor_count)
        text = build_embedding_text(
            name=row.get("name"),
            wikipedia_extract=row.get("wikipedia_extract"),
            anchor_concept_names=anchors,
        )
        store.update_embedding_text(category_id, text or None)
        rebuilt += 1
    LOGGER.info("epfl_graph: rebuilt embedding_text for %d categories", rebuilt)
    return rebuilt
