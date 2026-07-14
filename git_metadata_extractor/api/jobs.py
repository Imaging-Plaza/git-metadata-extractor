from __future__ import annotations

import logging
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from typing import Annotated, Any

from fastapi import Depends, Path, Request, status
from fastapi.responses import JSONResponse

from git_metadata_extractor.api_models import (
    V2ErrorResponse,
    V2ErrorType,
    V2ExtractJob,
    V2ExtractJobStatus,
    V2JobStatus,
)
from git_metadata_extractor.auth import verify_token

MIN_SUPPORTED_PYTHON = (3, 10)
PACKAGE_NAME = "git-metadata-extractor"
try:
    PACKAGE_VERSION = package_version(PACKAGE_NAME)
except PackageNotFoundError:
    PACKAGE_VERSION = "unknown"

MIN_SUBRESOURCE_PATH_SEGMENTS = 3
SUBRESOURCE_SEGMENT_INDEX = 2
JSONLD_CONTEXT_FALLBACK = {
    "schema": "http://schema.org/",
    "pulse": "https://open-pulse.epfl.ch/ontology#",
    "org": "http://www.w3.org/ns/org#",
}

logger = logging.getLogger(__name__)


from . import _helpers
from ._router import v2_router

def _maybe_mark_extract_job_stale(
    record: V2ExtractJob, job_store: Any,
) -> V2ExtractJob:
    """Flip an orphaned RUNNING job to FAILED in place.

    The worker executing this job may have died (OS kill, deploy, OOM, …)
    without flipping the status, leaving the stored record stuck in
    RUNNING. We detect that when no heartbeat has landed in over
    ``_helpers._JOB_STALE_THRESHOLD_SECONDS``, flip to FAILED, persist, and return
    the updated record so the client stops polling. New `POST /v2/extract`
    calls start a fresh job. No-op for non-RUNNING or still-fresh jobs.

    Shared by `GET /v2/jobs/{job_id}` (full record) and
    `GET /v2/crawl/{job_id}` (compact status) so both agree on liveness.
    """
    if record.status != V2ExtractJobStatus.RUNNING:
        return record
    now = datetime.now(timezone.utc)
    beat = record.last_heartbeat_at or record.started_at or record.submitted_at
    if beat is not None and (now - beat).total_seconds() > _helpers._JOB_STALE_THRESHOLD_SECONDS:
        stale_seconds = (now - beat).total_seconds()
        logger.warning(
            "marking job %s as FAILED: no heartbeat for %.0fs "
            "(threshold=%.0fs) — worker likely died mid-flight",
            record.job_id,
            stale_seconds,
            _helpers._JOB_STALE_THRESHOLD_SECONDS,
        )
        record.status = V2ExtractJobStatus.FAILED
        record.completed_at = now
        record.error = V2ErrorResponse(
            error_type=V2ErrorType.PIPELINE_ERROR,
            detail=(
                "extract job orphaned: worker process died mid-extraction "
                f"(no heartbeat for {int(stale_seconds)}s)"
            ),
            source_url=record.request.source_url,
        )
        job_store.set(record)
    return record


def _resolve_extract_record(
    request: Request, job_id: str,
) -> V2ExtractJob | JSONResponse:
    """Resolve an extract job by id, with liveness check.

    Returns the (stale-checked) :class:`V2ExtractJob`, or a `JSONResponse`
    error: 503 when the async job store is unavailable, 404 when no job
    matches. Shared by `GET /v2/jobs/{job_id}` and `GET /v2/crawl/{job_id}`.
    """
    job_store = _helpers._resolve_job_store(request)
    if job_store is None:
        error_payload = V2ErrorResponse(
            error_type=V2ErrorType.PIPELINE_ERROR,
            detail="async job store unavailable: provider cache is disabled",
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=error_payload.model_dump(mode="json", exclude_none=True),
        )
    record = job_store.get(job_id)
    if record is None:
        error_payload = V2ErrorResponse(
            error_type=V2ErrorType.NOT_FOUND,
            detail=f"no extract job found with id '{job_id}'",
        )
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=error_payload.model_dump(mode="json", exclude_none=True),
        )
    return _maybe_mark_extract_job_stale(record, job_store)


@v2_router.get(
    "/jobs/{job_id}",
    response_model=V2ExtractJob,
    response_model_exclude_none=True,
)
async def extract_job(
    job_id: Annotated[str, Path(description="Job id returned by POST /v2/extract.")],
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> V2ExtractJob | JSONResponse:
    """Retrieve a previously submitted extraction job (full record + graph)."""

    return _resolve_extract_record(request, job_id)


@v2_router.post(
    "/jobs/{job_id}/cancel",
    response_model=V2ExtractJob,
    response_model_exclude_none=True,
    tags=["Extraction"],
)
async def cancel_extract_job(
    job_id: Annotated[str, Path(description="Job id returned by POST /v2/extract.")],
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> V2ExtractJob | JSONResponse:
    """Cancel a pending/running extract job and free its worker.

    Cooperative async cancellation: cancels the job's asyncio task (which
    interrupts the pipeline at its next ``await`` — e.g. an in-flight LLM or
    HTTP call) and marks the record ``cancelled``. Idempotent: a job already in
    a terminal state is returned unchanged. 404 if no job matches, 503 if the
    async job store is unavailable.
    """
    resolved = _resolve_extract_record(request, job_id)
    if isinstance(resolved, JSONResponse):
        return resolved
    if resolved.status in _helpers._TERMINAL_JOB_STATUSES:
        return resolved

    task = _helpers._get_job_task(request, job_id)
    if task is not None and not task.done():
        task.cancel()

    # Mark cancelled immediately for an authoritative response even if the task
    # is wedged on a blocking call; the task's own CancelledError handler is a
    # no-op then (status already terminal).
    job_store = _helpers._resolve_job_store(request)
    if job_store is not None:
        record = job_store.get(job_id)
        if record is not None and record.status not in _helpers._TERMINAL_JOB_STATUSES:
            record.status = V2ExtractJobStatus.CANCELLED
            record.completed_at = datetime.now(timezone.utc)
            record.last_heartbeat_at = record.completed_at
            job_store.set(record)
            resolved = record
    return resolved


@v2_router.get(
    "/crawl/{job_id}",
    response_model=V2JobStatus,
    response_model_exclude_none=True,
    tags=["Extraction"],
)
async def crawl_status(
    job_id: Annotated[str, Path(description="Job id returned by POST /v2/extract.")],
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> V2JobStatus | JSONResponse:
    """Lightweight status of an extract job, without the result graph.

    Parity with the retired v1 crawl-status surface and a cheap polling target:
    returns just the lifecycle fields (status + timestamps + error). The
    full extracted graph lives at ``result_url`` (`GET /v2/jobs/{job_id}`).
    503 if the async job store is unavailable, 404 if no job matches.
    """
    resolved = _resolve_extract_record(request, job_id)
    if isinstance(resolved, JSONResponse):
        return resolved
    return V2JobStatus(
        job_id=resolved.job_id,
        status=resolved.status,
        source_url=resolved.request.source_url,
        submitted_at=resolved.submitted_at,
        started_at=resolved.started_at,
        completed_at=resolved.completed_at,
        last_heartbeat_at=resolved.last_heartbeat_at,
        error=resolved.error,
        result_url=f"/v2/jobs/{resolved.job_id}",
    )

