from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from copy import deepcopy
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from time import perf_counter
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import Depends, Path, Query, Request, status
from fastapi.responses import JSONResponse

from git_metadata_extractor.agents import ProviderSet, parse_agent_runtime
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
    V2FieldError,
    V2JSONLDOutput,
    V2JSONOutputEnvelope,
)
from git_metadata_extractor.auth import verify_token
from git_metadata_extractor.config import V2Config
from git_metadata_extractor.dependencies import get_provider_set
from git_metadata_extractor.providers.cache import (
    ProviderCache,
    cache_refresh_active,
    reset_cache_refresh,
    set_cache_refresh,
)
from git_metadata_extractor.providers.detection import UnsupportedGitHubURL, classify_github_url
from git_metadata_extractor.jobs import JobStore
from git_metadata_extractor.observation.query_log import QueryLog, query_log_var
from git_metadata_extractor.pipeline.run import (
    ASSEMBLED_CHAIN,
    OUTPUT_CHAIN,
    PAYLOAD_CHAIN,
    RECONCILE_CHAIN,
    RECONCILED_CHAIN,
    REFINEMENT_CHAIN,
    RESOLVER_CHAIN,
)
from git_metadata_extractor.pipeline.runner import StageError, run_pipeline
from git_metadata_extractor.pipeline.state import PipelineState
from git_metadata_extractor.pipeline.stages import (
    AssembledOutput,
    RootEntityValidationError,
    build_json_output,
    compute_stats,
)
from git_metadata_extractor.pipeline.stages.context_gather import RequiredProviderUnavailableError
from git_metadata_extractor.schema import load_jsonld_context

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


from . import _helpers, auto_ingest
from ._router import v2_router

async def _run_extract_job(
    *,
    payload: V2ExtractRequest,
    request: Request,
    providers: ProviderSet,
    job_store: JobStore,
    job_id: str,
) -> None:
    """Execute the extract pipeline for a job and persist the outcome.

    Runs the existing GET handler as a plain async function, then unwraps the
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
                try:
                    await asyncio.sleep(_helpers._JOB_HEARTBEAT_INTERVAL_SECONDS)
                except asyncio.CancelledError:
                    raise
                try:
                    current = job_store.get(job_id)
                    if current is None or current.status != V2ExtractJobStatus.RUNNING:
                        return
                    current.last_heartbeat_at = datetime.now(timezone.utc)
                    job_store.set(current)
                except Exception:
                    logger.exception("heartbeat write failed for job %s", job_id)

        heartbeat_task = asyncio.create_task(_heartbeat())

        result = await extract(
            full_path=payload.source_url,
            request=request,
            output_format=payload.output_format,
            agent_runtime=payload.agent_runtime,
            include_context_summary=payload.include_context_summary,
            include_internal_fields=payload.include_internal_fields,
            providers=providers,
            _token="",
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
            with contextlib.suppress(Exception):
                await heartbeat_task
        # Release pooled provider HTTP sessions at end of job (Bug 03). The
        # background job owns this ProviderSet for its whole lifetime, and the
        # post-extract auto-ingest hooks build their own providers, so nothing
        # uses these after the job completes.
        with contextlib.suppress(Exception):
            providers.close()


def _append_unique_warning(warnings: list[str], warning: str) -> None:
    if warning and warning not in warnings:
        warnings.append(warning)


def _iter_reconciled_entities(
    *,
    reconciled_entities: dict[str, list[dict[str, Any]]],
    memberships: list[dict[str, Any]],
    contributions: list[dict[str, Any]],
) -> list[tuple[str, dict[str, Any]]]:
    payloads: list[tuple[str, dict[str, Any]]] = []
    for entity_type, entities in (
        ("person", reconciled_entities.get("persons", [])),
        ("organization", reconciled_entities.get("organizations", [])),
        ("repository", reconciled_entities.get("repositories", [])),
        ("article", reconciled_entities.get("articles", [])),
    ):
        payloads.extend((entity_type, entity) for entity in entities if isinstance(entity, dict))
    payloads.extend(("membership", membership) for membership in memberships if isinstance(membership, dict))
    payloads.extend(
        ("contribution", contribution)
        for contribution in contributions
        if isinstance(contribution, dict)
    )
    return payloads


def _extract_jsonld_context() -> dict[str, Any]:
    try:
        return load_jsonld_context()
    except (OSError, TypeError, ValueError):
        return dict(JSONLD_CONTEXT_FALLBACK)


def _root_entity_type_for_detected_type(
    detected_type: Literal["repository", "user", "organization"],
) -> Literal["repository", "person", "organization"]:
    if detected_type == "user":
        return "person"
    return detected_type


def _build_rootless_assembled_output(
    *,
    reconciled: Any,
    strict_batch: Any,
    root_warning: str,
) -> AssembledOutput:
    related_entities: list[dict[str, Any]] = []
    for _, payload in strict_batch.valid_entities:
        if isinstance(payload, dict):
            related_entities.append(deepcopy(payload))

    excluded_entities: list[dict[str, Any]] = []
    warnings = [*reconciled.link_warnings, root_warning]
    for entity_type, payload, validation in strict_batch.invalid_entities:
        if not isinstance(payload, dict):
            continue
        reason = list(validation.errors)
        excluded_entities.append(
            {
                "entity_type": entity_type,
                "entity": deepcopy(payload),
                "reason": reason,
            },
        )
        warnings.append(
            f"Excluded {entity_type} entity '{payload.get('id')}' due to strict validation errors: {reason}",
        )

    return AssembledOutput(
        root_entity=None,
        related_entities=related_entities,
        excluded_entities=excluded_entities,
        warnings=warnings,
    )




@v2_router.get(
    "/extract/{full_path:path}",
    response_model=V2ExtractResponse,
    response_model_exclude_none=True,
)
async def extract(  # noqa: C901, PLR0911, PLR0912, PLR0913, PLR0915
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
    agent_runtime: Annotated[Literal["rule_based", "llm", "hybrid"] | None, Query()] = None,
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
    providers: Annotated[ProviderSet, Depends(get_provider_set)],
    _token: Annotated[str, Depends(verify_token)],
) -> V2ExtractResponse | JSONResponse:
    """Run the v2 extraction pipeline for a GitHub path."""

    config = V2Config()
    run_id: str | None = None

    try:
        classification = classify_github_url(full_path)
    except UnsupportedGitHubURL as exc:
        logger.warning("classify_url: unsupported url=%s reason=%s", exc.normalized_url, exc.reason)
        error_payload = V2ErrorResponse(
            error_type=V2ErrorType.UNSUPPORTED_URL,
            detail=exc.reason,
            source_url=exc.normalized_url,
            detected_path_kind=_helpers._extract_path_kind(full_path),
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=error_payload.model_dump(mode="json", exclude_none=True),
        )
    except ValueError as exc:
        logger.warning("classify_url: invalid input %s: %s", full_path, exc)
        error_payload = V2ErrorResponse(
            error_type=V2ErrorType.UNSUPPORTED_URL,
            detail=str(exc),
            source_url=full_path,
            detected_path_kind=_helpers._extract_path_kind(full_path),
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=error_payload.model_dump(mode="json", exclude_none=True),
        )

    run_id = str(uuid4())
    request.state.v2_run_id = run_id
    resolved_runtime = parse_agent_runtime(
        agent_runtime,
        default=config.V2_AGENT_RUNTIME_DEFAULT,
        field_name="agent_runtime",
    )
    total_started_at = perf_counter()
    # A wall clock as well as the monotonic one: `prov:startedAtTime` is a
    # timestamp, and `perf_counter()` has no epoch.
    run_started_at = datetime.now(timezone.utc)
    link_veracity_seconds = 0.0
    logger.info(
        "extract: run_id=%s url=%s detected_type=%s runtime=%s",
        run_id,
        classification.normalized_url,
        classification.detected_type.value,
        resolved_runtime.value,
    )

    # Per-request query log: every external-service tool call lands here.
    query_log = QueryLog(
        run_id=run_id,
        extract_full_path=classification.normalized_url,
    )
    query_log_var.set(query_log)

    # Resolve the cache singleton once. Sub-stages always receive it (so
    # provider caches, agent verdicts, Selenium fetches, link-veracity, and
    # DuckDuckGo searches all keep working). The pipeline-level lookup/store
    # is gated separately by V2_PIPELINE_CACHE_ENABLED so it can be toggled
    # off without disabling the sub-caches.
    pipeline_cache = getattr(request.app.state, "v2_provider_cache", None)
    if not isinstance(pipeline_cache, ProviderCache):
        pipeline_cache = None
    pipeline_cache_enabled = pipeline_cache is not None and _helpers._is_pipeline_cache_enabled()
    pipeline_cache_key: str | None = None
    if pipeline_cache_enabled:
        pipeline_cache_key = ProviderCache.make_key(
            "pipeline",
            "extract",
            url=classification.normalized_url,
            output_format=output_format,
            agent_runtime=resolved_runtime.value,
            include_context_summary=bool(include_context_summary),
            include_internal_fields=bool(include_internal_fields),
        )
        # Backfill refresh: skip the read so the pipeline re-runs with current
        # logic; the fresh result is still written back to `pipeline_cache_key`.
        cached_response = None if cache_refresh_active() else pipeline_cache.get(pipeline_cache_key)
        if isinstance(cached_response, dict):
            try:
                response_model = V2ExtractResponse.model_validate(cached_response)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "pipeline cache hit but cached payload failed validation; "
                    "falling through to fresh extraction (run_id=%s)",
                    run_id,
                )
            else:
                cached_seconds = perf_counter() - total_started_at
                logger.info(
                    "pipeline cache hit: url=%s run_id=%s elapsed=%s",
                    classification.normalized_url,
                    run_id,
                    _helpers._format_duration(cached_seconds),
                )
                return response_model

    try:
        orchestrator = _helpers._get_orchestrator(request)
        execution_plan = orchestrator.get_execution_plan(classification.detected_type)
        pipeline_result = await orchestrator.execute(
            plan=execution_plan,
            providers=providers,
            context={
                "source_url": classification.normalized_url,
                "url_info": classification,
                "agent_runtime": resolved_runtime.value,
                "run_id": run_id,
            },
        )
    except RequiredProviderUnavailableError as exc:
        logger.exception("provider_preflight failed: run_id=%s", run_id)
        error_payload = V2ErrorResponse(
            error_type=V2ErrorType.PROVIDER_ERROR,
            detail=str(exc),
            source_url=classification.normalized_url,
        )
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content=error_payload.model_dump(mode="json", exclude_none=True),
        )
    except Exception as exc:
        logger.exception("pipeline_execute failed: run_id=%s", run_id)
        error_payload = V2ErrorResponse(
            error_type=V2ErrorType.PIPELINE_ERROR,
            detail=str(exc),
            source_url=classification.normalized_url,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error_payload.model_dump(mode="json", exclude_none=True),
        )

    warnings = list(pipeline_result.warnings)

    pipeline_outputs_for_prompt = {
        agent_name: agent_result.data
        for agent_name, agent_result in pipeline_result.agent_results.items()
        if isinstance(agent_result.data, dict) and agent_result.data
    }
    gathered_context = (
        deepcopy(pipeline_result.gathered_context)
        if isinstance(pipeline_result.gathered_context, dict)
        else {}
    )

    typed_entity_buckets = pipeline_result.resolved_typed_entity_buckets().to_dict()
    # === entity chains ============================================
    # permissive_validation -> llm_dedup -> reconciliation
    #   -> the four ROR resolvers
    #   -> llm_critic / refine_with_llm
    #   -> guarantee_repo_author -> validate_org_github_handles
    #
    # One state threaded through all four chains rather than four separately
    # constructed ones: `buckets` becomes `reconciled` in the first chain and
    # every later stage reads it from the same object, which is what
    # PipelineState exists for. Order, gates and per-stage fail-open behaviour
    # all live in pipeline/run.py.
    entity_state = PipelineState(
        run_id=run_id,
        classification=classification,
        runtime=resolved_runtime,
        providers=providers,
        max_concurrent_agents=orchestrator.max_concurrent_agents,
        pipeline_outputs=pipeline_outputs_for_prompt,
        gathered_context=gathered_context,
        cache=pipeline_cache,
        buckets=typed_entity_buckets,
        warnings=warnings,
        started_at=run_started_at,
    )
    try:
        for chain in (
            RECONCILE_CHAIN,
            RESOLVER_CHAIN,
            REFINEMENT_CHAIN,
            RECONCILED_CHAIN,
        ):
            await run_pipeline(entity_state, chain)
    except StageError as exc:
        # Re-raise the original error so FastAPI's handler sees the same
        # exception type the inline sequence used to let escape.
        raise exc.cause from None

    llm_dedup_executed = bool(entity_state.extras.get("llm_dedup_executed", False))
    llm_critic_executed = bool(entity_state.extras.get("llm_critic_executed", False))


    jsonld_context = _extract_jsonld_context()

    # === output chain =============================================
    # strict_validation -> output_assembly -> critic exclusions ->
    # link_veracity (with its id-promotion and pruning follow-ups).
    # `reconciled` becomes `assembled` here.
    entity_state.output_format = output_format
    entity_state.include_internal_fields = bool(include_internal_fields)
    entity_state.include_context_summary = bool(include_context_summary)
    entity_state.jsonld_context = jsonld_context
    try:
        await run_pipeline(entity_state, OUTPUT_CHAIN)
    except StageError as exc:
        if isinstance(exc.cause, RootEntityValidationError):
            # A root that fails strict validation is a request error, not a
            # server error: the response lists the offending fields. Mapping
            # the domain exception to a status code stays here, in the HTTP
            # layer, rather than inside the stage.
            root_error = exc.cause
            failure_message = (
                f"Root {root_error.entity_type} entity "
                f"'{root_error.entity_id}' failed strict validation"
            )
            logger.warning("%s: %s", _helpers.STAGE_OUTPUT_ASSEMBLY, failure_message)
            error_payload = V2ErrorResponse(
                error_type=V2ErrorType.VALIDATION_ERROR,
                detail=failure_message,
                source_url=classification.normalized_url,
                errors=[
                    V2FieldError(
                        field=error.get("path", "<root>"),
                        message=error.get("message", "validation error"),
                        value=error.get("expected"),
                    )
                    for error in root_error.validation_errors
                ],
            )
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                content=error_payload.model_dump(mode="json", exclude_none=True),
            )
        raise exc.cause from None

    assembled_output = entity_state.assembled


    # === assembled-output chain =================================
    # Twelve stages that all take an AssembledOutput and return
    # (AssembledOutput, warnings). They live in pipeline/run.py as an ordered
    # list; see that module for why the names are log names and why almost
    # every one is fail-closed.
    pipeline_state = entity_state
    try:
        await run_pipeline(pipeline_state, ASSEMBLED_CHAIN)
    except StageError as exc:
        # Preserve the route's previous behaviour: these stages were not
        # wrapped, so the original exception reached FastAPI as a 500.
        raise exc.cause from None

    assembled_output = pipeline_state.assembled
    link_veracity_seconds = pipeline_state.extras.get(
        "link_veracity_seconds",
        link_veracity_seconds,
    )

    # === payload chain ============================================
    # rule_based_disciplines -> concept_tagging -> jsonld_build ->
    # shacl_gate. `assembled` becomes `payload`; the sequence ends here and
    # the response is built below.
    try:
        await run_pipeline(pipeline_state, PAYLOAD_CHAIN)
    except StageError as exc:
        raise exc.cause from None

    assembled_output = pipeline_state.assembled
    shacl_graph_payload = pipeline_state.payload


    response_output: V2JSONLDOutput | V2JSONOutputEnvelope
    if output_format == "jsonld":
        response_output = V2JSONLDOutput.model_validate(shacl_graph_payload)
    else:
        response_output = V2JSONOutputEnvelope.model_validate(
            build_json_output(assembled_output),
        )
    context_summary_markdown: str | None = None
    if include_context_summary:
        summary_value = gathered_context.get("compiled_context_markdown")
        if isinstance(summary_value, str):
            normalized_summary = summary_value.strip()
            if normalized_summary:
                context_summary_markdown = summary_value

    output_payload = response_output.model_dump(mode="json", by_alias=True)
    extract_graph = _helpers._jsonld_to_graph(output_payload) if output_format == "jsonld" else None
    final_entities = []
    if isinstance(assembled_output.root_entity, dict):
        final_entities.append(assembled_output.root_entity)
    final_entities.extend(assembled_output.related_entities)
    final_entity_count = len(final_entities)

    completed_stages = list(pipeline_result.stages_completed)
    stage_sequence = [_helpers.STAGE_PERMISSIVE_VALIDATION]
    if llm_dedup_executed:
        stage_sequence.append(_helpers.STAGE_LLM_DEDUP)
    stage_sequence.append(_helpers.STAGE_RECONCILIATION)
    if llm_critic_executed:
        stage_sequence.append(_helpers.STAGE_LLM_CRITIC)
    stage_sequence.extend(
        [
            _helpers.STAGE_STRICT_VALIDATION,
            _helpers.STAGE_OUTPUT_ASSEMBLY,
            _helpers.STAGE_LINK_VERACITY,
            _helpers.STAGE_JSONLD_BUILD,
            _helpers.STAGE_SHACL_GATE,
        ],
    )
    for stage_name in stage_sequence:
        if stage_name not in completed_stages:
            completed_stages.append(stage_name)
    stats = compute_stats(
        graph=extract_graph,
        run_id=run_id,
        duration_ms=pipeline_result.duration_ms,
        stages_completed=completed_stages,
        entities_count=final_entity_count,
    )

    total_seconds = perf_counter() - total_started_at
    orchestrator_seconds = pipeline_result.duration_ms / 1000.0
    other_seconds = max(0.0, total_seconds - orchestrator_seconds - link_veracity_seconds)
    logger.info(
        "extract complete: run_id=%s url=%s entities=%d warnings=%d "
        "total=%s (orchestrator=%s, link_veracity=%s, other=%s)",
        run_id,
        classification.normalized_url,
        final_entity_count,
        len(warnings),
        _helpers._format_duration(total_seconds),
        _helpers._format_duration(orchestrator_seconds),
        _helpers._format_duration(link_veracity_seconds),
        _helpers._format_duration(other_seconds),
    )

    response_model = V2ExtractResponse(
        source_url=classification.normalized_url,
        detected_type=classification.detected_type.value,
        output_format=output_format,
        output=response_output,
        context_summary_markdown=context_summary_markdown,
        warnings=warnings,
        stats=stats,
        extraction_run=pipeline_state.extras.get("extraction_run"),
        substrate=pipeline_state.substrate,
    )

    if pipeline_cache is not None and pipeline_cache_key is not None:
        try:
            pipeline_cache.set(
                pipeline_cache_key,
                response_model.model_dump(mode="json", exclude_none=True),
            )
        except Exception:
            logger.exception(
                "failed to write pipeline cache (run_id=%s)",
                run_id,
            )

    written_log_path = query_log.write()
    if written_log_path is not None:
        logger.info("query log written: %s", written_log_path)

    # Auto-ingest hook (Bug-class extension): when the operator opts in
    # via `V2_GITHUB_REPOS_RAG_AUTO_INGEST=true`, every successful repository
    # extract triggers a background ingest of the repo card into the
    # GitHub RAG DuckDB + Qdrant collection. This grows the index
    # organically as new repos are seen, so subsequent extractions find
    # them via `search_github_rag`. Fire-and-forget — the caller's
    # response is already built, the ingest is best-effort.
    auto_ingest._maybe_schedule_github_repos_auto_ingest(
        classification=classification,
        run_id=run_id,
    )
    auto_ingest._maybe_schedule_github_users_auto_ingest(
        classification=classification,
        run_id=run_id,
    )
    auto_ingest._maybe_schedule_github_orgs_auto_ingest(
        classification=classification,
        run_id=run_id,
    )

    return response_model




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
    providers: Annotated[ProviderSet, Depends(get_provider_set)],
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

