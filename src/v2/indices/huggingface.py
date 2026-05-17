"""Async ingest helper for the HuggingFace index, called from `/v2/indices/huggingface/ingest`.

Dispatches each ``HFIngestItem`` to the per-repo helper exposed by the index
(``ingest_single_model`` / ``ingest_single_dataset`` / ``ingest_single_space``)
and persists a per-item outcome under the shared
:class:`IndexIngestJobStore`. Config / DuckDB store / HF client are loaded
lazily on first request and cached on ``app.state.v2_huggingface_resources``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from src.v2.api_models import (
    HFIngestItem,
    HuggingFaceIngestRequest,
    IndexIngestJob,
    IndexIngestJobStatus,
)

if TYPE_CHECKING:
    from src.v2.indices.jobs import IndexIngestJobStore

logger = logging.getLogger(__name__)

INDEX_NAME = "huggingface"


def get_or_create_huggingface_resources(app_state: Any) -> Any | None:
    """Lazy-init (config, client, store) for the HF index on ``app.state``.

    Returns ``None`` if the HuggingFace index module isn't importable. The
    triple is cached on ``app_state.v2_huggingface_resources``.
    """

    cached = getattr(app_state, "v2_huggingface_resources", None)
    if cached is not None:
        return cached
    try:
        from src.index.huggingface.config import load_config  # noqa: PLC0415
        from src.index.huggingface.ingest.hf_client import HFClient  # noqa: PLC0415
        from src.index.huggingface.storage.duckdb_store import DuckDBStore  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001 — optional dependency
        logger.warning("huggingface ingest: index module unavailable — %s", exc)
        return None
    try:
        config = load_config()
        client = HFClient(config)
        store = DuckDBStore.open()
    except Exception as exc:  # noqa: BLE001
        logger.warning("huggingface ingest: resource init failed — %s", exc)
        return None
    app_state.v2_huggingface_resources = (config, client, store)
    return app_state.v2_huggingface_resources


def _ingest_one_item(
    item: HFIngestItem,
    *,
    config: Any,
    client: Any,
    store: Any,
) -> dict[str, Any]:
    """Dispatch a single ``HFIngestItem`` to its per-repo helper. Never raises."""

    try:
        if item.type == "model":
            from src.index.huggingface.ingest.models_ingest import (  # noqa: PLC0415
                ingest_single_model,
            )
            persisted = ingest_single_model(
                repo_id=item.repo_id, config=config, client=client, store=store,
            )
        elif item.type == "dataset":
            from src.index.huggingface.ingest.datasets_ingest import (  # noqa: PLC0415
                ingest_single_dataset,
            )
            persisted = ingest_single_dataset(
                repo_id=item.repo_id, config=config, client=client, store=store,
            )
        else:
            from src.index.huggingface.ingest.spaces_ingest import (  # noqa: PLC0415
                ingest_single_space,
            )
            persisted = ingest_single_space(
                repo_id=item.repo_id, config=config, client=client, store=store,
            )
    except Exception as exc:  # noqa: BLE001 — one-item failure must not kill the job
        logger.warning(
            "huggingface ingest: %s/%s failed — %s",
            item.type, item.repo_id, exc,
        )
        return {
            "type": item.type, "repo_id": item.repo_id,
            "persisted": False, "error": str(exc),
        }
    return {
        "type": item.type, "repo_id": item.repo_id,
        "persisted": bool(persisted),
    }


async def run_huggingface_ingest_job(
    *,
    payload: HuggingFaceIngestRequest,
    app_state: Any,
    job_store: IndexIngestJobStore,
    job_id: str,
) -> None:
    """Background task: ingest each item in ``payload.items`` and persist the outcome."""

    try:
        existing = job_store.get(job_id)
        if existing is None:
            return
        existing.status = IndexIngestJobStatus.RUNNING
        existing.started_at = datetime.now(timezone.utc)
        job_store.set(existing)

        resources = get_or_create_huggingface_resources(app_state)
        if resources is None:
            existing.status = IndexIngestJobStatus.FAILED
            existing.completed_at = datetime.now(timezone.utc)
            existing.error = "huggingface index module unavailable on this deployment"
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
        persisted = sum(1 for r in items_results if r.get("persisted"))
        failed = sum(1 for r in items_results if r.get("error"))
        finished.summary = {
            "requested": len(payload.items),
            "persisted": persisted,
            "failed": failed,
            "items": items_results,
        }
        job_store.set(finished)
    except Exception as exc:
        logger.exception("huggingface ingest job %s failed", job_id)
        record = job_store.get(job_id)
        if record is None:
            return
        record.status = IndexIngestJobStatus.FAILED
        record.completed_at = datetime.now(timezone.utc)
        record.error = str(exc)
        job_store.set(record)


__all__ = [
    "INDEX_NAME",
    "get_or_create_huggingface_resources",
    "run_huggingface_ingest_job",
]
