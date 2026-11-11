# ruff: noqa: SLF001 - shared private API adapter helpers
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Annotated, Any, Literal
from uuid import uuid4

from fastapi import Depends, Path, Query, Request, status
from fastapi.responses import JSONResponse

from git_metadata_extractor.agents import (
    ProviderSet,  # noqa: TC001 - FastAPI resolves route annotations
)
from git_metadata_extractor.agents.llm.runtime import (
    reset_request_model_override,
    set_request_model_override,
)
from git_metadata_extractor.api_models import (
    V2ErrorResponse,
    V2ErrorType,
    V2ExtractJob,
    V2ExtractJobAccepted,
    V2ExtractJobStatus,
    V2ExtractRequest,
    V2ExtractResponse,
)
from git_metadata_extractor.auth import verify_token
from git_metadata_extractor.dependencies import (
    defer_provider_resources,
    provider_dependency,
)
from git_metadata_extractor.providers.cache import (
    reset_cache_refresh,
    set_cache_refresh,
)
from git_metadata_extractor.providers.detection import (
    UnsupportedGitHubURL,
    classify_github_url,
)
from git_metadata_extractor.services.extraction import (
    ExtractionError,
    ExtractionOptions,
    execute_extraction,
)

from . import _helpers, auto_ingest
from ._router import v2_router

if TYPE_CHECKING:
    from git_metadata_extractor.jobs import JobStore


logger = logging.getLogger(__name__)


async def _run_extract_job(  # noqa: C901, PLR0915 - explicit job lifecycle
    *,
    payload: V2ExtractRequest,
    request: Request,
    providers: ProviderSet,
    job_store: JobStore,
    job_id: str,
) -> None:
    """Execute the extract pipeline for a job and persist the outcome.

    Runs the shared extraction adapter, then unwraps the
    success/error result onto the persisted V2ExtractJob record.
    """
    heartbeat_task: asyncio.Task[Any] | None = None
    override_token = set_request_model_override(
        payload.model_override.model_dump(exclude_none=True)
        if payload.model_override is not None
        else None,
    )
    refresh_token = set_cache_refresh(payload.refresh)
    try:
        existing = job_store.get(job_id)
        if existing is None:
            return
        now = datetime.now(timezone.utc)
        existing.status = V2ExtractJobStatus.RUNNING
        existing.started_at = now
        existing.last_heartbeat_at = now
        job_store.set(existing)

        # Periodic heartbeat so a job whose worker process dies mid-flight
        # can be detected and marked failed by the GET endpoint instead
        # of staying "running" forever. Cancelled in the finally block
        # so we don't leave a zombie task behind on normal completion.
        async def _heartbeat() -> None:
            while True:
                await asyncio.sleep(_helpers._JOB_HEARTBEAT_INTERVAL_SECONDS)
                try:
                    current = job_store.get(job_id)
                    if current is None or current.status != V2ExtractJobStatus.RUNNING:
                        return
                    current.last_heartbeat_at = datetime.now(timezone.utc)
                    job_store.set(current)
                except Exception:
                    logger.exception("heartbeat write failed for job %s", job_id)

        heartbeat_task = asyncio.create_task(_heartbeat())

        result = await _extract_for_request(
            full_path=payload.source_url,
            request=request,
            output_format=payload.output_format,
            agent_runtime=payload.agent_runtime,
            include_context_summary=payload.include_context_summary,
            include_internal_fields=payload.include_internal_fields,
            providers=providers,
        )

        finished = job_store.get(job_id) or existing
        finished.completed_at = datetime.now(timezone.utc)
        finished.last_heartbeat_at = finished.completed_at
        if isinstance(result, V2ExtractResponse):
            finished.status = V2ExtractJobStatus.COMPLETED
            finished.result = result
        else:
            finished.status = V2ExtractJobStatus.FAILED
            try:
                error_payload = json.loads(result.body)
                finished.error = V2ErrorResponse.model_validate(error_payload)
            except Exception:  # noqa: BLE001
                finished.error = V2ErrorResponse(
                    error_type=V2ErrorType.PIPELINE_ERROR,
                    detail="extraction failed with non-decodable error payload",
                    source_url=payload.source_url,
                )
        job_store.set(finished)
    except asyncio.CancelledError:
        # Cooperative cancel via POST /v2/jobs/{id}/cancel (task.cancel()).
        # Mark the record cancelled, then re-raise so the task ends cleanly.
        logger.info("extract job %s cancelled", job_id)
        record = job_store.get(job_id)
        if record is not None and record.status not in _helpers._TERMINAL_JOB_STATUSES:
            record.status = V2ExtractJobStatus.CANCELLED
            record.completed_at = datetime.now(timezone.utc)
            record.last_heartbeat_at = record.completed_at
            job_store.set(record)
        raise
    except Exception as exc:
        logger.exception("extract job %s failed", job_id)
        record = job_store.get(job_id)
        if record is None:
            return
        record.status = V2ExtractJobStatus.FAILED
        record.completed_at = datetime.now(timezone.utc)
        record.last_heartbeat_at = record.completed_at
        record.error = V2ErrorResponse(
            error_type=V2ErrorType.PIPELINE_ERROR,
            detail=str(exc),
            source_url=payload.source_url,
        )
        job_store.set(record)
    finally:
        reset_request_model_override(override_token)
        reset_cache_refresh(refresh_token)
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await heartbeat_task
        resources = getattr(request.state, "v2_provider_resources", None)
        if resources is not None:
            await resources.aclose()


@v2_router.get(
    "/extract/{full_path:path}",
    response_model=V2ExtractResponse,
    response_model_exclude_none=True,
)
async def extract(  # noqa: PLR0913
    full_path: Annotated[
        str,
        Path(
            description="GitHub repository, user, or organization URL or handle.",
            openapi_examples={
                "gimie": {
                    "summary": "GIMIE repository",
                    "value": "https://github.com/sdsc-ordes/gimie",
                },
                "sdsc-ordes": {
                    "summary": "SDSC ORDES organization",
                    "value": "https://github.com/sdsc-ordes",
                },
                "cmdoret": {
                    "summary": "cmdoret user",
                    "value": "https://github.com/cmdoret",
                },
            },
        ),
    ],
    request: Request,
    *,
    output_format: Annotated[Literal["jsonld", "json"], Query()] = "jsonld",
    agent_runtime: Annotated[
        Literal["rule_based", "llm", "hybrid"] | None, Query(),
    ] = None,
    include_context_summary: Annotated[bool, Query()] = False,
    include_internal_fields: Annotated[
        bool,
        Query(
            description=(
                "When true, the response keeps `_`-prefixed internal fields "
                "(e.g. `_bio`, `_avatar_url`, `_orcid_keywords`, `_company`) "
                "that aren't part of the open-pulse ontology yet. Strict SHACL "
                "validation still runs identically — this flag only affects "
                "what the consumer sees. Default false for ontology compliance."
            ),
        ),
    ] = False,
    providers: Annotated[ProviderSet, Depends(provider_dependency)],
    _token: Annotated[str, Depends(verify_token)],
) -> V2ExtractResponse | JSONResponse:
    """Run extraction through the transport-independent application service."""
    return await _extract_for_request(
        full_path=full_path,
        request=request,
        providers=providers,
        agent_runtime=agent_runtime,
        output_format=output_format,
        include_context_summary=include_context_summary,
        include_internal_fields=include_internal_fields,
    )


async def _extract_for_request(  # noqa: PLR0913 - public route options
    *,
    full_path,
    request,
    providers,
    agent_runtime,
    output_format,
    include_context_summary,
    include_internal_fields,
):
    options = ExtractionOptions(
        source_url=full_path,
        agent_runtime=agent_runtime,
        output_format=output_format,
        include_context_summary=include_context_summary,
        include_internal_fields=include_internal_fields,
        read_only=bool(getattr(request.state, "v2_read_only", False)),
    )
    try:
        result = await execute_extraction(
            options,
            providers=providers,
            orchestrator=_helpers._get_orchestrator(request),
            cache=getattr(request.app.state, "v2_provider_cache", None),
            execution_profile=getattr(request.app.state, "v2_execution_profile", None),
            classification=getattr(request.state, "v2_classification", None),
        )
    except ExtractionError as exc:
        codes = {
            V2ErrorType.UNSUPPORTED_URL: 422,
            V2ErrorType.VALIDATION_ERROR: 422,
            V2ErrorType.PROVIDER_ERROR: 502,
            V2ErrorType.PIPELINE_ERROR: 500,
        }
        return JSONResponse(
            status_code=codes[exc.error.error_type],
            content=exc.error.model_dump(mode="json", exclude_none=True),
        )
    request.state.v2_run_id = result.run_id
    if not options.read_only and not result.cache_hit:
        for schedule in (
            auto_ingest._maybe_schedule_github_repos_auto_ingest,
            auto_ingest._maybe_schedule_github_users_auto_ingest,
            auto_ingest._maybe_schedule_github_orgs_auto_ingest,
        ):
            schedule(classification=result.classification, run_id=result.run_id)
    return result.response


@v2_router.post(
    "/extract",
    response_model=V2ExtractJobAccepted,
    response_model_exclude_none=True,
    status_code=status.HTTP_202_ACCEPTED,
)
async def extract_post(
    payload: V2ExtractRequest,
    request: Request,
    *,
    providers: Annotated[ProviderSet, Depends(provider_dependency)],
    _token: Annotated[str, Depends(verify_token)],
) -> V2ExtractJobAccepted | JSONResponse:
    """Submit an extraction asynchronously. Returns a job id to poll via GET."""

    try:
        classification = classify_github_url(payload.source_url)
    except UnsupportedGitHubURL as exc:
        error_payload = V2ErrorResponse(
            error_type=V2ErrorType.UNSUPPORTED_URL,
            detail=exc.reason,
            source_url=exc.normalized_url,
            detected_path_kind=_helpers._extract_path_kind(payload.source_url),
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=error_payload.model_dump(mode="json", exclude_none=True),
        )
    except ValueError as exc:
        error_payload = V2ErrorResponse(
            error_type=V2ErrorType.UNSUPPORTED_URL,
            detail=str(exc),
            source_url=payload.source_url,
            detected_path_kind=_helpers._extract_path_kind(payload.source_url),
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=error_payload.model_dump(mode="json", exclude_none=True),
        )

    job_store = _helpers._resolve_job_store(request)
    if job_store is None:
        error_payload = V2ErrorResponse(
            error_type=V2ErrorType.PIPELINE_ERROR,
            detail="async job store unavailable: provider cache is disabled",
            source_url=classification.normalized_url,
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=error_payload.model_dump(mode="json", exclude_none=True),
        )

    job_id = str(uuid4())
    submitted_at = datetime.now(timezone.utc)
    normalized_payload = payload.model_copy(
        update={"source_url": classification.normalized_url},
    )
    job = V2ExtractJob(
        job_id=job_id,
        status=V2ExtractJobStatus.PENDING,
        request=normalized_payload,
        submitted_at=submitted_at,
    )
    job_store.set(job)

    task = asyncio.create_task(
        _run_extract_job(
            payload=normalized_payload,
            request=request,
            providers=providers,
            job_store=job_store,
            job_id=job_id,
        ),
    )
    defer_provider_resources(request, task)
    _helpers._track_background_task(request, task)
    _helpers._register_job_task(request, job_id, task)

    logger.info(
        "extract job submitted: job_id=%s url=%s detected_type=%s",
        job_id,
        classification.normalized_url,
        classification.detected_type.value,
    )
    return V2ExtractJobAccepted(
        job_id=job_id,
        status=V2ExtractJobStatus.PENDING,
        status_url=_helpers._job_status_path(job_id),
        submitted_at=submitted_at,
    )
