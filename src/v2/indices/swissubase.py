"""Async ingest helper for the SWISSUbase index, called from `/v2/indices/swissubase/ingest`."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from src.v2.api_models import IndexIngestJobStatus, SwissubaseIngestRequest

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
        finished.status = IndexIngestJobStatus.COMPLETED
        finished.completed_at = datetime.now(timezone.utc)
        persisted = sum(1 for r in items_results if r["outcome"] == "persisted")
        not_found = sum(1 for r in items_results if r["outcome"] == "not_found")
        skipped = sum(
            1 for r in items_results if r["outcome"] == "projection_skipped"
        )
        errors = sum(1 for r in items_results if r["outcome"] == "error")
        finished.summary = {
            "requested": len(payload.study_ids),
            "persisted": persisted,
            "not_found": not_found,
            "projection_skipped": skipped,
            "errors": errors,
            "items": items_results,
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


__all__ = [
    "INDEX_NAME",
    "get_or_create_swissubase_resources",
    "run_swissubase_ingest_job",
]
