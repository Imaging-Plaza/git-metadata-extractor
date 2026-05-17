"""Async ingest helper for the Zenodo index, called from `/v2/indices/zenodo/ingest`.

Wraps :func:`src.index.zenodo.ingest.records.ingest_by_ids` in a background
task that persists the outcome on the shared :class:`IndexIngestJobStore`.
The store and config are loaded lazily on first request and cached on
``app.state.v2_zenodo_store`` so subsequent requests reuse them.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from src.v2.api_models import (
    IndexIngestJob,
    IndexIngestJobStatus,
    ZenodoIngestRequest,
)

if TYPE_CHECKING:
    from src.v2.indices.jobs import IndexIngestJobStore

logger = logging.getLogger(__name__)

INDEX_NAME = "zenodo"


def get_or_create_zenodo_store(app_state: Any) -> Any | None:
    """Lazy-init the ZenodoStore + config on ``app.state``.

    Returns ``None`` if the Zenodo index module isn't importable (e.g. the
    deployment is shipped without it). The returned tuple-shaped value is
    cached on ``app_state.v2_zenodo_resources`` for subsequent reuse.
    """

    cached = getattr(app_state, "v2_zenodo_resources", None)
    if cached is not None:
        return cached
    try:
        from src.index.zenodo.config import load_config  # noqa: PLC0415
        from src.index.zenodo.storage.duckdb_store import ZenodoStore  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001 — optional dependency
        logger.warning("zenodo ingest: index module unavailable — %s", exc)
        return None
    try:
        config = load_config()
        store = ZenodoStore.open(config.paths.duckdb_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("zenodo ingest: store init failed — %s", exc)
        return None
    app_state.v2_zenodo_resources = (config, store)
    return app_state.v2_zenodo_resources


async def run_zenodo_ingest_job(
    *,
    payload: ZenodoIngestRequest,
    app_state: Any,
    job_store: IndexIngestJobStore,
    job_id: str,
) -> None:
    """Background task: run the Zenodo ingest and persist the outcome."""

    try:
        existing = job_store.get(job_id)
        if existing is None:
            return
        existing.status = IndexIngestJobStatus.RUNNING
        existing.started_at = datetime.now(timezone.utc)
        job_store.set(existing)

        resources = get_or_create_zenodo_store(app_state)
        if resources is None:
            existing.status = IndexIngestJobStatus.FAILED
            existing.completed_at = datetime.now(timezone.utc)
            existing.error = "zenodo index module unavailable on this deployment"
            job_store.set(existing)
            return
        config, store = resources

        from src.index.zenodo.ingest.records import (  # noqa: PLC0415
            _normalize_id_token,
            ingest_by_ids,
        )

        normalized: list[str] = []
        unparsed: list[str] = []
        seen: set[str] = set()
        for token in payload.ids:
            rid = _normalize_id_token(token)
            if rid is None:
                unparsed.append(token)
                continue
            if rid in seen:
                continue
            seen.add(rid)
            normalized.append(rid)

        if not normalized:
            existing.status = IndexIngestJobStatus.FAILED
            existing.completed_at = datetime.now(timezone.utc)
            existing.error = (
                "no parseable Zenodo ids in request "
                f"(received {len(payload.ids)}, "
                f"unparsed={unparsed[:5]})"
            )
            job_store.set(existing)
            return

        summary = await asyncio.to_thread(
            ingest_by_ids,
            config=config,
            store=store,
            ids=normalized,
            refresh=payload.refresh,
        )
        if isinstance(summary, dict) and unparsed:
            summary = {**summary, "unparsed_input": unparsed}

        finished = job_store.get(job_id) or existing
        finished.status = IndexIngestJobStatus.COMPLETED
        finished.completed_at = datetime.now(timezone.utc)
        finished.summary = summary if isinstance(summary, dict) else {"result": summary}
        job_store.set(finished)
    except Exception as exc:
        logger.exception("zenodo ingest job %s failed", job_id)
        record = job_store.get(job_id)
        if record is None:
            return
        record.status = IndexIngestJobStatus.FAILED
        record.completed_at = datetime.now(timezone.utc)
        record.error = str(exc)
        job_store.set(record)


__all__ = ["INDEX_NAME", "get_or_create_zenodo_store", "run_zenodo_ingest_job"]
