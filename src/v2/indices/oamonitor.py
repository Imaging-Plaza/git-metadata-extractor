"""Async ingest + sync search helpers for the OAM-CH index.

Mounted at ``POST /v2/indices/oamonitor/{ingest,search}``. Ingest accepts
``{items: [{entity, id}]}`` and dispatches each item to the per-entity
``ingest_single_*`` helper. Search reuses the uniform :class:`IndexSearchRequest`
with ``target`` selecting one of ``journals | publications | publishers |
organisations``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from src.v2.api_models import (
    IndexIngestJobStatus,
    IndexSearchRequest,
    IndexSearchResponse,
    OamonitorIngestItem,
    OamonitorIngestRequest,
)
from src.v2.indices._search_common import hit_from_raw

if TYPE_CHECKING:
    from src.v2.indices.jobs import IndexIngestJobStore

logger = logging.getLogger(__name__)

INDEX_NAME = "oamonitor"


def get_or_create_oamonitor_resources(app_state: Any) -> Any | None:
    """Lazy-init (config, client, store) on ``app.state``."""

    cached = getattr(app_state, "v2_oamonitor_resources", None)
    if cached is not None:
        return cached
    try:
        from src.index.oamonitor.config import load_config  # noqa: PLC0415
        from src.index.oamonitor.ingest.oamonitor_client import (  # noqa: PLC0415
            OamonitorClient,
        )
        from src.index.oamonitor.storage.duckdb_store import (  # noqa: PLC0415
            OamonitorStore,
        )
    except Exception as exc:  # noqa: BLE001 — optional dependency
        logger.warning("oamonitor ingest: index module unavailable — %s", exc)
        return None
    try:
        config = load_config()
        client = OamonitorClient(config)
        store = OamonitorStore.open(config.paths.duckdb_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("oamonitor ingest: resource init failed — %s", exc)
        return None
    app_state.v2_oamonitor_resources = (config, client, store)
    return app_state.v2_oamonitor_resources


def _ingest_one_item(
    item: OamonitorIngestItem,
    *,
    config: Any, client: Any, store: Any,
) -> dict[str, Any]:
    """Dispatch a single ``OamonitorIngestItem`` to its per-entity helper."""
    try:
        if item.entity == "journals":
            from src.index.oamonitor.ingest.journals import (  # noqa: PLC0415
                ingest_single_journal,
            )
            outcome = ingest_single_journal(
                config=config, client=client, store=store, journal_id=item.id,
            )
        elif item.entity == "publications":
            from src.index.oamonitor.ingest.publications import (  # noqa: PLC0415
                ingest_single_publication,
            )
            outcome = ingest_single_publication(
                config=config, client=client, store=store, publication_id=item.id,
            )
        elif item.entity == "publishers":
            from src.index.oamonitor.ingest.publishers import (  # noqa: PLC0415
                ingest_single_publisher,
            )
            outcome = ingest_single_publisher(
                config=config, client=client, store=store, publisher_id=item.id,
            )
        else:
            from src.index.oamonitor.ingest.organisations import (  # noqa: PLC0415
                ingest_single_organisation,
            )
            outcome = ingest_single_organisation(
                config=config, client=client, store=store, organisation_id=item.id,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "oamonitor ingest: %s/%s failed — %s", item.entity, item.id, exc,
        )
        return {
            "entity": item.entity, "id": item.id,
            "outcome": "error", "error": str(exc),
        }
    return {"entity": item.entity, "id": item.id, "outcome": outcome}


async def run_oamonitor_ingest_job(
    *,
    payload: OamonitorIngestRequest,
    app_state: Any,
    job_store: IndexIngestJobStore,
    job_id: str,
) -> None:
    """Background task: ingest each item and persist the outcome."""

    try:
        existing = job_store.get(job_id)
        if existing is None:
            return
        existing.status = IndexIngestJobStatus.RUNNING
        existing.started_at = datetime.now(timezone.utc)
        job_store.set(existing)

        resources = get_or_create_oamonitor_resources(app_state)
        if resources is None:
            existing.status = IndexIngestJobStatus.FAILED
            existing.completed_at = datetime.now(timezone.utc)
            existing.error = "oamonitor index module unavailable on this deployment"
            job_store.set(existing)
            return
        config, client, store = resources

        items_results: list[dict[str, Any]] = []
        for item in payload.items:
            result = await asyncio.to_thread(
                _ingest_one_item,
                item, config=config, client=client, store=store,
            )
            items_results.append(result)

        finished = job_store.get(job_id) or existing
        finished.status = IndexIngestJobStatus.COMPLETED
        finished.completed_at = datetime.now(timezone.utc)
        persisted = sum(1 for r in items_results if r["outcome"] == "persisted")
        not_found = sum(1 for r in items_results if r["outcome"] == "not_found")
        rejected = sum(1 for r in items_results if r["outcome"] == "rejected")
        errors = sum(1 for r in items_results if r["outcome"] == "error")
        finished.summary = {
            "requested": len(payload.items),
            "persisted": persisted,
            "not_found": not_found,
            "rejected": rejected,
            "errors": errors,
            "items": items_results,
        }
        job_store.set(finished)
    except Exception as exc:
        logger.exception("oamonitor ingest job %s failed", job_id)
        record = job_store.get(job_id)
        if record is None:
            return
        record.status = IndexIngestJobStatus.FAILED
        record.completed_at = datetime.now(timezone.utc)
        record.error = str(exc)
        job_store.set(record)


async def run_oamonitor_search(
    payload: IndexSearchRequest, app_state: Any,
) -> IndexSearchResponse | None:
    """Run a semantic search against one OAM-CH entity collection."""
    resources = get_or_create_oamonitor_resources(app_state)
    if resources is None:
        return None
    config, _, store = resources
    entity_type = payload.target or "journals"
    from src.index.oamonitor.retrieval.semantic import (  # noqa: PLC0415
        semantic_search,
    )
    try:
        raw_hits = await asyncio.to_thread(
            semantic_search,
            config=config,
            query=payload.query,
            entity_type=entity_type,
            top_k=payload.top_k,
            candidate_k=payload.candidate_k or max(payload.top_k * 5, 50),
            filter_payload=payload.filter_payload,
            store=store,
        )
    except ValueError as exc:
        # Invalid entity_type lands here; surface as empty hit list with a tag
        # rather than a 500 so the API contract stays clean.
        logger.warning("oamonitor search: invalid target %r (%s)", entity_type, exc)
        return IndexSearchResponse(
            index_name="oamonitor",
            target=entity_type,
            query=payload.query,
            hits=[],
            extra={"detail": str(exc)},
        )
    return IndexSearchResponse(
        index_name="oamonitor",
        target=entity_type,
        query=payload.query,
        hits=[hit_from_raw(h) for h in raw_hits],
    )


__all__ = [
    "INDEX_NAME",
    "get_or_create_oamonitor_resources",
    "run_oamonitor_ingest_job",
    "run_oamonitor_search",
]
