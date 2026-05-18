"""Async ingest helper for the Renkulab index, called from `/v2/indices/renkulab/ingest`.

v1 scope: only project records. Groups / users / data_connectors will follow
when their per-id fetch helpers exist on the Renku client.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from src.v2.api_models import IndexIngestJobStatus, RenkulabIngestRequest

if TYPE_CHECKING:
    from src.v2.indices.jobs import IndexIngestJobStore

logger = logging.getLogger(__name__)

INDEX_NAME = "renkulab"


def get_or_create_renkulab_resources(app_state: Any) -> Any | None:
    """Lazy-init (config, client, store) on ``app.state``."""

    cached = getattr(app_state, "v2_renkulab_resources", None)
    if cached is not None:
        return cached
    try:
        from src.index.renkulab.config import load_config  # noqa: PLC0415
        from src.index.renkulab.ingest.renku_client import (  # noqa: PLC0415
            RenkulabClient,
        )
        from src.index.renkulab.storage.duckdb_store import (  # noqa: PLC0415
            RenkulabStore,
        )
    except Exception as exc:  # noqa: BLE001 — optional dependency
        logger.warning("renkulab ingest: index module unavailable — %s", exc)
        return None
    try:
        config = load_config()
        client = RenkulabClient(config)
        store = RenkulabStore.open(config.paths.duckdb_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("renkulab ingest: resource init failed — %s", exc)
        return None
    app_state.v2_renkulab_resources = (config, client, store)
    return app_state.v2_renkulab_resources


async def _ingest_one_project(
    project_id: str, *, client: Any, store: Any,
) -> dict[str, Any]:
    """Run a single per-project ingest; never raises."""
    try:
        from src.index.renkulab.ingest.pipeline import (  # noqa: PLC0415
            ingest_single_project,
        )
        outcome = await ingest_single_project(
            client=client, store=store, project_id=project_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("renkulab ingest: %s failed — %s", project_id, exc)
        return {"project_id": project_id, "outcome": "error", "error": str(exc)}
    return {"project_id": project_id, "outcome": outcome}


async def run_renkulab_ingest_job(
    *,
    payload: RenkulabIngestRequest,
    app_state: Any,
    job_store: IndexIngestJobStore,
    job_id: str,
) -> None:
    """Background task: ingest each project id and persist the outcome."""

    try:
        existing = job_store.get(job_id)
        if existing is None:
            return
        existing.status = IndexIngestJobStatus.RUNNING
        existing.started_at = datetime.now(timezone.utc)
        job_store.set(existing)

        resources = get_or_create_renkulab_resources(app_state)
        if resources is None:
            existing.status = IndexIngestJobStatus.FAILED
            existing.completed_at = datetime.now(timezone.utc)
            existing.error = "renkulab index module unavailable on this deployment"
            job_store.set(existing)
            return
        _, client, store = resources

        items_results: list[dict[str, Any]] = []
        for project_id in payload.project_ids:
            result = await _ingest_one_project(
                project_id, client=client, store=store,
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
            "requested": len(payload.project_ids),
            "persisted": persisted,
            "not_found": not_found,
            "projection_skipped": skipped,
            "errors": errors,
            "items": items_results,
        }
        job_store.set(finished)
    except Exception as exc:
        logger.exception("renkulab ingest job %s failed", job_id)
        record = job_store.get(job_id)
        if record is None:
            return
        record.status = IndexIngestJobStatus.FAILED
        record.completed_at = datetime.now(timezone.utc)
        record.error = str(exc)
        job_store.set(record)


__all__ = [
    "INDEX_NAME",
    "get_or_create_renkulab_resources",
    "run_renkulab_ingest_job",
]
