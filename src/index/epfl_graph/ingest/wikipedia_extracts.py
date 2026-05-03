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
MAX_PAGE_IDS_PER_REQUEST = 50
RATE_PER_SECOND = 5
DEFAULT_TIMEOUT = 30
MAX_EXTRACT_CHARS_FOR_EMBED = 1200  # cap each extract for embedding text


def _fetch_extracts_batch_by_title(  # noqa: C901
    titles: list[str], *, session: requests.Session, timeout: float,
) -> dict[str, str]:
    """Title-keyed extract fetch. Returns ``{original_title: extract}``.

    MediaWiki normalizes/redirects titles transparently with ``redirects=1``;
    we map results back to the *input* title using the ``normalized`` and
    ``redirects`` from→to arrays.
    """
    if not titles:
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
            "redirects": "1",
            "titles": "|".join(titles),
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    query = payload.get("query") if isinstance(payload, dict) else None
    if not isinstance(query, dict):
        return {}
    # Resolved title → input title (chain: input → normalized → redirected).
    resolved_to_input: dict[str, str] = {t: t for t in titles}
    for entry in query.get("normalized", []) or []:
        if not isinstance(entry, dict):
            continue
        from_title = entry.get("from")
        to_title = entry.get("to")
        if isinstance(from_title, str) and isinstance(to_title, str):
            input_title = resolved_to_input.get(from_title, from_title)
            resolved_to_input[to_title] = input_title
    for entry in query.get("redirects", []) or []:
        if not isinstance(entry, dict):
            continue
        from_title = entry.get("from")
        to_title = entry.get("to")
        if isinstance(from_title, str) and isinstance(to_title, str):
            input_title = resolved_to_input.get(from_title, from_title)
            resolved_to_input[to_title] = input_title

    pages = query.get("pages") if isinstance(query.get("pages"), dict) else {}
    out: dict[str, str] = {}
    for page in pages.values():
        if not isinstance(page, dict):
            continue
        extract = page.get("extract")
        title = page.get("title")
        if not isinstance(extract, str) or not extract.strip():
            continue
        if not isinstance(title, str):
            continue
        input_title = resolved_to_input.get(title, title)
        out[input_title] = extract.strip()
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

    # Use the canonical Wikipedia title (stored in `categories.name`) instead
    # of pageids — `name` matches the target article exactly when no
    # rename has happened, and `redirects=1` handles the rest. Keying on
    # title also makes the redirect/normalization mapping straightforward.
    title_to_category: dict[str, str] = {}
    for row in pending:
        name = row.get("name")
        category_id = row.get("category_id")
        if not isinstance(name, str) or not name.strip() or not category_id:
            continue
        title_to_category.setdefault(name.strip(), str(category_id))

    titles = list(title_to_category.keys())
    for index in range(0, len(titles), MAX_PAGE_IDS_PER_REQUEST):
        batch = titles[index : index + MAX_PAGE_IDS_PER_REQUEST]
        now = time.monotonic()
        wait = max(0.0, last_call + interval - now)
        if wait:
            time.sleep(wait)
        last_call = time.monotonic()

        try:
            extracts = _fetch_extracts_batch_by_title(
                batch, session=session, timeout=DEFAULT_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(
                "epfl_graph: wikipedia batch failed at offset %d: %s", index, exc,
            )
            continue

        for input_title, extract in extracts.items():
            category_id = title_to_category.get(input_title)
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
                updated, len(titles),
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
