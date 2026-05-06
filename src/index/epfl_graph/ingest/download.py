"""Walk the EPFL Graph ontology tree and persist categories to DuckDB.

For each node returned by ``ontology_tree()`` we call
``category_info(category_id)`` to pull the canonical name (from the
backing Wikipedia article), depth, parent, child categories, and the
top-N anchor concepts that define that category. The full payload is
stored in the ``raw`` JSON column so downstream consumers can reach into
it without a re-fetch.

A flat token-bucket rate limit is applied (defaults to ~5 req/s) so the
~2226-node walk finishes in roughly 8 minutes without abusing the
graphai endpoint.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, Any

from src.index.epfl_graph.storage.duckdb_store import EpflGraphStore
from src.module.epfl_graph.ontology import (
    GRAPHSEARCH_CATEGORY_URL,
    category_info,
    ontology_tree,
)

if TYPE_CHECKING:
    from src.index.epfl_graph.config import EpflGraphIndexConfig

LOGGER = logging.getLogger(__name__)


def _build_embedding_text(
    *,
    name: str | None,
    info_block: dict[str, Any],
    concepts: list[dict[str, Any]],
    max_concepts: int,
) -> str:
    pieces: list[str] = []
    if name:
        pieces.append(name)
    info_name = info_block.get("name") if isinstance(info_block, dict) else None
    if isinstance(info_name, str) and info_name and info_name != name:
        pieces.append(info_name)
    concept_names = []
    for concept in concepts[:max_concepts]:
        if not isinstance(concept, dict):
            continue
        cname = concept.get("name") or concept.get("concept_name")
        if isinstance(cname, str) and cname.strip():
            concept_names.append(cname.strip())
    if concept_names:
        pieces.append("Anchor concepts: " + ", ".join(concept_names))
    return ". ".join(pieces).strip()


def _wikipedia_url_for(info_block: dict[str, Any], name: str | None) -> str | None:
    page_id = info_block.get("id") if isinstance(info_block, dict) else None
    if isinstance(page_id, (int, str)) and str(page_id).strip():
        return "https://en.wikipedia.org/?curid=" + str(page_id).strip()
    if name:
        return "https://en.wikipedia.org/wiki/" + name.replace(" ", "_")
    return None


def _ingest_one(
    category_id: str,
    *,
    parent_id: str | None,
    max_concepts: int,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]] | None:
    payload = category_info(category_id, use_cache=False)
    if not isinstance(payload, dict) or not payload:
        return None
    info_block = payload.get("info") if isinstance(payload, dict) else {}
    info = info_block if isinstance(info_block, dict) else {}
    name = info.get("name") if isinstance(info.get("name"), str) else None
    depth = info.get("depth") if isinstance(info.get("depth"), int) else None
    page_id_raw = info.get("id")
    page_id = (
        str(page_id_raw).strip()
        if isinstance(page_id_raw, (int, str)) and str(page_id_raw).strip()
        else None
    )
    raw_concepts = payload.get("concepts") if isinstance(payload, dict) else None
    if not isinstance(raw_concepts, list):
        raw_concepts = []
    concepts = [c for c in raw_concepts if isinstance(c, dict)]
    children = (
        payload.get("child_categories")
        if isinstance(payload, dict)
        else None
    )
    n_children = len(children) if isinstance(children, list) else 0
    parent_in_payload = payload.get("parent_category")
    resolved_parent = (
        parent_in_payload
        if isinstance(parent_in_payload, str) and parent_in_payload
        else parent_id
    )
    embedding_text = _build_embedding_text(
        name=name, info_block=info, concepts=concepts, max_concepts=max_concepts,
    )
    row = {
        "category_id": category_id,
        "name": name,
        "depth": depth,
        "parent_id": resolved_parent,
        "wikipedia_page_id": page_id,
        "wikipedia_url": _wikipedia_url_for(info, name),
        "graphsearch_url": GRAPHSEARCH_CATEGORY_URL + category_id,
        "n_concepts": len(concepts),
        "n_children": n_children,
        "embedding_text": embedding_text or None,
    }
    return row, payload, concepts


def ingest_tree(  # noqa: C901, PLR0915
    config: EpflGraphIndexConfig,
    *,
    limit: int | None = None,
    log_every: int = 100,
) -> int:
    """Walk the ontology tree and upsert every node into the local DuckDB.

    Returns the number of categories successfully ingested.
    """
    config.require_rcp()  # not used here, but keeps env-error contract aligned
    LOGGER.info("epfl_graph: fetching ontology tree...")
    tree = ontology_tree()
    edges = tree.get("child_to_parent") if isinstance(tree, dict) else None
    if not isinstance(edges, list):
        msg = "ontology_tree() returned no child_to_parent edges"
        raise RuntimeError(msg)  # noqa: TRY004

    parent_map: dict[str, str] = {}
    nodes: set[str] = set()
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        child = edge.get("child_id")
        parent = edge.get("parent_id")
        if isinstance(child, str) and child:
            nodes.add(child)
            if isinstance(parent, str) and parent:
                parent_map[child] = parent
                nodes.add(parent)

    targets = sorted(nodes)
    if limit is not None:
        targets = targets[:limit]
    LOGGER.info("epfl_graph: %d categories to ingest", len(targets))

    store = EpflGraphStore.open(config.paths.duckdb_path)
    interval = 1.0 / max(1, config.graphai.rate_per_second)
    workers = max(1, config.graphai.max_concurrency)
    max_concepts = config.graphai.anchor_concepts_per_category
    last_call = 0.0
    ingested = 0

    def _paced_fetch(category_id: str) -> Any:
        nonlocal last_call
        now = time.monotonic()
        wait = max(0.0, last_call + interval - now)
        if wait:
            time.sleep(wait)
        last_call = time.monotonic()
        return _ingest_one(
            category_id,
            parent_id=parent_map.get(category_id),
            max_concepts=max_concepts,
        )

    try:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_paced_fetch, cid): cid for cid in targets
            }
            for index, future in enumerate(as_completed(futures), start=1):
                cid = futures[future]
                try:
                    result = future.result()
                except Exception as exc:  # noqa: BLE001
                    LOGGER.warning(
                        "epfl_graph: failed to fetch %s: %s", cid, exc,
                    )
                    continue
                if result is None:
                    continue
                row, payload, concepts = result
                try:
                    store.upsert_category(row, payload, concepts)
                    ingested += 1
                except Exception as exc:  # noqa: BLE001
                    LOGGER.warning(
                        "epfl_graph: failed to upsert %s: %s", cid, exc,
                    )
                    continue
                if index % log_every == 0:
                    LOGGER.info(
                        "epfl_graph: ingested %d / %d", index, len(targets),
                    )
    finally:
        store.close()

    LOGGER.info(
        "epfl_graph: ingest complete — %d categories upserted", ingested,
    )
    return ingested
