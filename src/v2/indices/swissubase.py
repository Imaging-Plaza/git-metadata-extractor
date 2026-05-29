"""Async ingest helper for the SWISSUbase index, called from `/v2/indices/swissubase/ingest`."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from src.v2.api_models import (
    IndexIngestJobStatus,
    IndexSearchRequest,
    IndexSearchResponse,
    SwissubaseIngestRequest,
)
from src.v2.indices._embed_step import run_embed_step
from src.v2.indices._search_common import hit_from_raw

if TYPE_CHECKING:
    from src.v2.indices.jobs import IndexIngestJobStore

logger = logging.getLogger(__name__)

INDEX_NAME = "swissubase"


def get_or_create_swissubase_resources(app_state: Any) -> Any | None:
    """Lazy-init (config, client, store, scope) on ``app.state``."""

    cached = getattr(app_state, "v2_swissubase_resources", None)
    if cached is not None:
        return cached
    try:
        from src.index.swissubase.config import load_config  # noqa: PLC0415
        from src.index.swissubase.ingest.scope import (  # noqa: PLC0415
            switzerland_scope,
        )
        from src.index.swissubase.ingest.swissubase_client import (  # noqa: PLC0415
            SwissubaseClient,
        )
        from src.index.swissubase.storage.duckdb_store import (  # noqa: PLC0415
            SwissubaseStore,
        )
    except Exception as exc:  # noqa: BLE001 — optional dependency
        logger.warning("swissubase ingest: index module unavailable — %s", exc)
        return None
    try:
        config = load_config()
        client = SwissubaseClient(config)
        store = SwissubaseStore.open(config.paths.duckdb_path)
        scope = switzerland_scope(config)
    except Exception as exc:  # noqa: BLE001
        logger.warning("swissubase ingest: resource init failed — %s", exc)
        return None
    app_state.v2_swissubase_resources = (config, client, store, scope)
    return app_state.v2_swissubase_resources


def _ingest_one_study(
    study_id: str, *, config: Any, client: Any, store: Any, scope: Any,
) -> dict[str, Any]:
    """Run a single per-study ingest; never raises."""
    try:
        from src.index.swissubase.ingest.studies import (  # noqa: PLC0415
            ingest_single_study,
        )
        outcome = ingest_single_study(
            config=config, client=client, store=store, scope=scope,
            study_id=study_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("swissubase ingest: %s failed — %s", study_id, exc)
        return {"study_id": study_id, "outcome": "error", "error": str(exc)}
    return {"study_id": study_id, "outcome": outcome}


async def run_swissubase_ingest_job(
    *,
    payload: SwissubaseIngestRequest,
    app_state: Any,
    job_store: IndexIngestJobStore,
    job_id: str,
) -> None:
    """Background task: ingest each study id and persist the outcome."""

    try:
        existing = job_store.get(job_id)
        if existing is None:
            return
        existing.status = IndexIngestJobStatus.RUNNING
        existing.started_at = datetime.now(timezone.utc)
        job_store.set(existing)

        resources = get_or_create_swissubase_resources(app_state)
        if resources is None:
            existing.status = IndexIngestJobStatus.FAILED
            existing.completed_at = datetime.now(timezone.utc)
            existing.error = "swissubase index module unavailable on this deployment"
            job_store.set(existing)
            return
        config, client, store, scope = resources

        items_results: list[dict[str, Any]] = []
        for study_id in payload.study_ids:
            result = await asyncio.to_thread(
                _ingest_one_study,
                study_id, config=config, client=client, store=store, scope=scope,
            )
            items_results.append(result)

        finished = job_store.get(job_id) or existing
        finished.completed_at = datetime.now(timezone.utc)
        persisted = sum(1 for r in items_results if r["outcome"] == "persisted")
        not_found = sum(1 for r in items_results if r["outcome"] == "not_found")
        skipped = sum(
            1 for r in items_results if r["outcome"] == "projection_skipped"
        )
        errors = sum(1 for r in items_results if r["outcome"] == "error")

        from src.index.swissubase.embed.pipeline import embed_entities  # noqa: PLC0415
        embed_summary = await run_embed_step(
            provider=INDEX_NAME,
            job_id=job_id,
            embed_call=lambda: embed_entities(config=config, store=store),
            checkpoint_store=store,
        )

        finished.status = IndexIngestJobStatus.COMPLETED
        finished.summary = {
            "requested": len(payload.study_ids),
            "persisted": persisted,
            "not_found": not_found,
            "projection_skipped": skipped,
            "errors": errors,
            "items": items_results,
            "embed": embed_summary,
        }
        job_store.set(finished)
    except Exception as exc:
        logger.exception("swissubase ingest job %s failed", job_id)
        record = job_store.get(job_id)
        if record is None:
            return
        record.status = IndexIngestJobStatus.FAILED
        record.completed_at = datetime.now(timezone.utc)
        record.error = str(exc)
        job_store.set(record)


async def run_swissubase_search(
    payload: IndexSearchRequest, app_state: Any,
) -> IndexSearchResponse | None:
    """Run a semantic search against the SWISSUbase index."""
    resources = get_or_create_swissubase_resources(app_state)
    if resources is None:
        return None
    config, _, store, _ = resources
    from src.index.swissubase.retrieval.semantic import semantic_search  # noqa: PLC0415
    raw_hits = await asyncio.to_thread(
        semantic_search,
        config=config, query=payload.query,
        top_k=payload.top_k,
        candidate_k=payload.candidate_k or max(payload.top_k * 5, 50),
        filter_payload=payload.filter_payload,
        store=store,
    )
    return IndexSearchResponse(
        index_name="swissubase",
        target=None,
        query=payload.query,
        hits=[hit_from_raw(h) for h in raw_hits],
    )


__all__ = [
    "INDEX_NAME",
    "get_or_create_swissubase_resources",
    "run_swissubase_ingest_job",
    "run_swissubase_search",
]
