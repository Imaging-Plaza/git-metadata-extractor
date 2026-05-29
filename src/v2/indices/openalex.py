"""Async ingest helper for the OpenAlex index, called from `/v2/indices/openalex/ingest`."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from src.v2.api_models import (
    IndexIngestJobStatus,
    IndexSearchRequest,
    IndexSearchResponse,
    OpenAlexIngestRequest,
)
from src.v2.indices._embed_step import run_embed_step
from src.v2.indices._search_common import hit_from_raw

if TYPE_CHECKING:
    from src.v2.indices.jobs import IndexIngestJobStore

logger = logging.getLogger(__name__)

INDEX_NAME = "openalex"


def get_or_create_openalex_resources(app_state: Any) -> Any | None:
    """Lazy-init (config, store) on ``app.state``."""

    cached = getattr(app_state, "v2_openalex_resources", None)
    if cached is not None:
        return cached
    try:
        from src.index.openalex.config import load_config  # noqa: PLC0415
        from src.index.openalex.storage.duckdb_store import OpenAlexStore  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001 — optional dependency
        logger.warning("openalex ingest: index module unavailable — %s", exc)
        return None
    try:
        config = load_config()
        store = OpenAlexStore.open()
    except Exception as exc:  # noqa: BLE001
        logger.warning("openalex ingest: resource init failed — %s", exc)
        return None
    app_state.v2_openalex_resources = (config, store)
    return app_state.v2_openalex_resources


def _ingest_one_work(
    work_id: str, *, config: Any, store: Any,
) -> dict[str, Any]:
    """Run a single per-work ingest; never raises."""
    try:
        from src.index.openalex.ingest.works import ingest_single_work  # noqa: PLC0415
        outcome = ingest_single_work(
            config=config, store=store, work_id=work_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("openalex ingest: %s failed — %s", work_id, exc)
        return {"id": work_id, "outcome": "error", "error": str(exc)}
    return {"id": work_id, "outcome": outcome}


async def run_openalex_ingest_job(
    *,
    payload: OpenAlexIngestRequest,
    app_state: Any,
    job_store: IndexIngestJobStore,
    job_id: str,
) -> None:
    """Background task: ingest each OpenAlex work id and persist the outcome."""

    try:
        existing = job_store.get(job_id)
        if existing is None:
            return
        existing.status = IndexIngestJobStatus.RUNNING
        existing.started_at = datetime.now(timezone.utc)
        job_store.set(existing)

        resources = get_or_create_openalex_resources(app_state)
        if resources is None:
            existing.status = IndexIngestJobStatus.FAILED
            existing.completed_at = datetime.now(timezone.utc)
            existing.error = "openalex index module unavailable on this deployment"
            job_store.set(existing)
            return
        config, store = resources

        items_results: list[dict[str, Any]] = []
        for work_id in payload.ids:
            result = await asyncio.to_thread(
                _ingest_one_work,
                work_id, config=config, store=store,
            )
            items_results.append(result)

        finished = job_store.get(job_id) or existing
        finished.completed_at = datetime.now(timezone.utc)
        persisted = sum(1 for r in items_results if r["outcome"] == "persisted")
        not_found = sum(1 for r in items_results if r["outcome"] == "not_found")
        rejected = sum(1 for r in items_results if r["outcome"] == "rejected")
        errors = sum(1 for r in items_results if r["outcome"] == "error")

        from src.index.openalex.embed.pipeline import embed_entities  # noqa: PLC0415
        from src.index.openalex.models import ALL_ENTITY_TYPES  # noqa: PLC0415
        embed_summary = await run_embed_step(
            provider=INDEX_NAME,
            job_id=job_id,
            embed_call=lambda: embed_entities(
                config=config, store=store, entity_types=list(ALL_ENTITY_TYPES),
            ),
        )

        finished.status = IndexIngestJobStatus.COMPLETED
        finished.summary = {
            "requested": len(payload.ids),
            "persisted": persisted,
            "not_found": not_found,
            "rejected": rejected,
            "errors": errors,
            "items": items_results,
            "embed": embed_summary,
        }
        job_store.set(finished)
    except Exception as exc:
        logger.exception("openalex ingest job %s failed", job_id)
        record = job_store.get(job_id)
        if record is None:
            return
        record.status = IndexIngestJobStatus.FAILED
        record.completed_at = datetime.now(timezone.utc)
        record.error = str(exc)
        job_store.set(record)


async def run_openalex_search(
    payload: IndexSearchRequest, app_state: Any,
) -> IndexSearchResponse | None:
    """Run a semantic search against the OpenAlex index.

    ``target`` picks the entity type (``works`` / ``authors`` /
    ``institutions`` / ``sources`` / ``topics`` / ``concepts``); defaults
    to ``works``.
    """
    resources = get_or_create_openalex_resources(app_state)
    if resources is None:
        return None
    config, store = resources
    entity_type = payload.target or "works"
    from src.index.openalex.retrieval.semantic import semantic_search  # noqa: PLC0415
    raw_hits = await asyncio.to_thread(
        semantic_search,
        config=config, query=payload.query, entity_type=entity_type,
        top_k=payload.top_k,
        candidate_k=payload.candidate_k or max(payload.top_k * 5, 50),
        filter_payload=payload.filter_payload,
        store=store,
    )
    return IndexSearchResponse(
        index_name="openalex",
        target=entity_type,
        query=payload.query,
        hits=[hit_from_raw(h) for h in raw_hits],
    )


__all__ = [
    "INDEX_NAME",
    "get_or_create_openalex_resources",
    "run_openalex_ingest_job",
    "run_openalex_search",
]
