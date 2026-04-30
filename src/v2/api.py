from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from copy import deepcopy
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from time import perf_counter
from typing import Annotated, Any, Literal
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import APIRouter, Depends, Path, Query, Request, status
from fastapi.responses import JSONResponse
from rdflib import Graph as RDFGraph

from src.v2.agents import AgentRuntime, ProviderSet, parse_agent_runtime
from src.v2.config import V2Config
from src.v2.dependencies import _resolve_provider_cache, get_provider_set
from src.v2.ingest.cache import ProviderCache
from src.v2.ingest.detection import UnsupportedGitHubURL, classify_github_url
from src.v2.jobs import JobStore
from src.v2.observation.query_log import QueryLog, query_log_var
from src.v2.schema import load_jsonld_context
from src.v2.api_models import (
    V2ErrorResponse,
    V2ErrorType,
    V2ExtractJob,
    V2ExtractJobAccepted,
    V2ExtractJobStatus,
    V2ExtractRequest,
    V2ExtractResponse,
    V2FieldError,
    V2HealthResponse,
    V2JSONLDOutput,
    V2JSONOutputEnvelope,
)
from src.v2.pipeline import PipelineOrchestrator
from src.v2.pipeline.stages import (
    AssembledOutput,
    RootEntityValidationError,
    apply_link_pruning_to_assembled_output,
    assemble_output,
    build_json_output,
    build_jsonld_output,
    compute_stats,
    infer_org_units,
    infer_owners,
    promote_failed_id_entities,
    reconcile_entities,
    run_llm_critic_stage,
    run_llm_dedup_stage,
    run_link_veracity_stage,
    validate_articles,
    validate_ownership,
)
from src.v2.pipeline.stages.context_gather import RequiredProviderUnavailableError
from src.v2.validation import (
    SHACLValidator,
    StrictSchemaValidator,
    load_ontology_shapes_graph,
)
from src.v2.validation.shacl_validation import SHACLRuntimeUnavailableError

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

STAGE_CLASSIFY_URL = "classify_url"
STAGE_PERMISSIVE_VALIDATION = "permissive_validation"
STAGE_STRICT_VALIDATION = "strict_validation"
STAGE_RECONCILIATION = "reconciliation"
STAGE_LLM_DEDUP = "llm_dedup"
STAGE_LLM_CRITIC = "llm_critic"
STAGE_SHACL_GATE = "shacl_gate"
STAGE_OUTPUT_ASSEMBLY = "output_assembly"
STAGE_JSONLD_BUILD = "jsonld_build"
STAGE_LINK_VERACITY = "link_veracity"

v2_router = APIRouter(prefix="/v2")


_TRUTHY_ENV_VALUES = {"1", "true", "t", "yes", "y", "on"}


def _is_pipeline_cache_enabled() -> bool:
    """Read `V2_PIPELINE_CACHE_ENABLED` env var (default true).

    When false, `extract()` neither reads nor writes the pipeline-level cache,
    so every request runs the full pipeline. Sub-level caches (provider
    responses, agent verdicts, Selenium fetches, link-veracity) are unaffected.
    """
    raw = os.getenv("V2_PIPELINE_CACHE_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() in _TRUTHY_ENV_VALUES


def _should_apply_critic_pruning() -> bool:
    """Read `V2_APPLY_CRITIC_PRUNING` env var (default false).

    When false (the default), the LLM critic stage is skipped entirely so no
    entities get dropped from the output. Set to true to re-enable the critic
    stage's drop suggestions.
    """
    raw = os.getenv("V2_APPLY_CRITIC_PRUNING")
    if raw is None:
        return False
    return raw.strip().lower() in _TRUTHY_ENV_VALUES


def _format_duration(seconds: float) -> str:
    """Render a duration in compact human form, e.g. '7.3s' or '4m 12s'."""
    if seconds < 60.0:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    remainder = seconds - minutes * 60
    return f"{minutes}m {remainder:.0f}s"


def _jsonld_to_graph(payload: dict[str, Any]) -> RDFGraph | None:
    try:
        graph = RDFGraph()
        graph.parse(data=json.dumps(payload), format="json-ld")
    except Exception:  # noqa: BLE001
        return None
    else:
        return graph


def _extract_path_kind(source_url: str) -> str | None:
    candidate_url = source_url.strip()
    if "://" not in candidate_url:
        candidate_url = f"https://{candidate_url}"

    parsed_url = urlparse(candidate_url)
    path_segments = [segment for segment in parsed_url.path.split("/") if segment]
    if len(path_segments) >= MIN_SUBRESOURCE_PATH_SEGMENTS:
        return path_segments[SUBRESOURCE_SEGMENT_INDEX].lower()
    return None


def _get_orchestrator(request: Request) -> PipelineOrchestrator:
    existing = getattr(request.app.state, "v2_orchestrator", None)
    if isinstance(existing, PipelineOrchestrator):
        return existing

    cache = getattr(request.app.state, "v2_provider_cache", None)
    if not isinstance(cache, ProviderCache):
        cache = None
    orchestrator = PipelineOrchestrator(cache=cache)
    request.app.state.v2_orchestrator = orchestrator
    return orchestrator


def _resolve_job_store(request: Request) -> JobStore | None:
    """Resolve a JobStore backed by the shared ProviderCache, if available."""
    cache = _resolve_provider_cache(request.app.state)
    if not isinstance(cache, ProviderCache):
        return None
    return JobStore(cache)


def _track_background_task(request: Request, task: asyncio.Task[Any]) -> None:
    """Hold a strong reference to a background task so it isn't GC'd mid-flight."""
    tasks: set[asyncio.Task[Any]] | None = getattr(
        request.app.state,
        "_v2_job_tasks",
        None,
    )
    if tasks is None:
        tasks = set()
        request.app.state._v2_job_tasks = tasks  # noqa: SLF001
    tasks.add(task)
    task.add_done_callback(tasks.discard)


def _job_status_path(job_id: str) -> str:
    return f"/v2/jobs/{job_id}"


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
    try:
        existing = job_store.get(job_id)
        if existing is None:
            return
        existing.status = V2ExtractJobStatus.RUNNING
        existing.started_at = datetime.now(timezone.utc)
        job_store.set(existing)

        result = await extract(
            full_path=payload.source_url,
            request=request,
            output_format=payload.output_format,
            agent_runtime=payload.agent_runtime,
            include_context_summary=payload.include_context_summary,
            providers=providers,
        )

        finished = job_store.get(job_id) or existing
        finished.completed_at = datetime.now(timezone.utc)
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
    except Exception as exc:  # noqa: BLE001
        logger.exception("extract job %s failed", job_id)
        record = job_store.get(job_id)
        if record is None:
            return
        record.status = V2ExtractJobStatus.FAILED
        record.completed_at = datetime.now(timezone.utc)
        record.error = V2ErrorResponse(
            error_type=V2ErrorType.PIPELINE_ERROR,
            detail=str(exc),
            source_url=payload.source_url,
        )
        job_store.set(record)


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
    agent_runtime: Annotated[Literal["rule_based", "llm"] | None, Query()] = None,
    include_context_summary: Annotated[bool, Query()] = False,
    providers: Annotated[ProviderSet, Depends(get_provider_set)],
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
            detected_path_kind=_extract_path_kind(full_path),
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
            detected_path_kind=_extract_path_kind(full_path),
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
    pipeline_cache_enabled = pipeline_cache is not None and _is_pipeline_cache_enabled()
    pipeline_cache_key: str | None = None
    if pipeline_cache_enabled:
        pipeline_cache_key = ProviderCache.make_key(
            "pipeline",
            "extract",
            url=classification.normalized_url,
            output_format=output_format,
            agent_runtime=resolved_runtime.value,
            include_context_summary=bool(include_context_summary),
        )
        cached_response = pipeline_cache.get(pipeline_cache_key)
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
                    _format_duration(cached_seconds),
                )
                return response_model

    try:
        orchestrator = _get_orchestrator(request)
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
    except Exception as exc:  # noqa: BLE001
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
    permissive_entity_count = sum(len(bucket) for bucket in typed_entity_buckets.values())
    logger.info("%s: entity_count=%d", STAGE_PERMISSIVE_VALIDATION, permissive_entity_count)

    llm_dedup_executed = False
    if resolved_runtime == AgentRuntime.LLM:
        llm_dedup_executed = True
        try:
            dedup_result = await run_llm_dedup_stage(
                typed_entity_buckets=typed_entity_buckets,
                source_url=classification.normalized_url,
                detected_type=classification.detected_type.value,
                providers=providers,
                pipeline_outputs=pipeline_outputs_for_prompt,
                initial_context=gathered_context,
                max_concurrency=orchestrator.max_concurrent_agents,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("%s stage failed", STAGE_LLM_DEDUP)
            _append_unique_warning(warnings, f"llm_dedup stage failed: {exc}")
        else:
            typed_entity_buckets = dedup_result.typed_entity_buckets
            logger.info(
                "%s: accepted=%d rejected=%d remap=%d",
                STAGE_LLM_DEDUP,
                dedup_result.accepted_cluster_count,
                dedup_result.rejected_cluster_count,
                dedup_result.remap_count,
            )
            for warning in dedup_result.warnings:
                _append_unique_warning(warnings, warning)

    llm_critic_executed = False
    critic_pruned_excluded_entities: list[dict[str, Any]] = []
    reconciled = reconcile_entities(typed_entity_buckets)
    logger.info(
        "%s: persons=%d orgs=%d repos=%d articles=%d memberships=%d contributions=%d",
        STAGE_RECONCILIATION,
        len(reconciled.entities.get("persons", [])),
        len(reconciled.entities.get("organizations", [])),
        len(reconciled.entities.get("repositories", [])),
        len(reconciled.entities.get("articles", [])),
        len(reconciled.memberships),
        len(reconciled.contributions),
    )
    for warning in reconciled.link_warnings:
        _append_unique_warning(warnings, warning)

    apply_critic_pruning = _should_apply_critic_pruning()
    if resolved_runtime == AgentRuntime.LLM and not apply_critic_pruning:
        logger.info(
            "%s: skipped (V2_APPLY_CRITIC_PRUNING=false — entities preserved)",
            STAGE_LLM_CRITIC,
        )
    if resolved_runtime == AgentRuntime.LLM and apply_critic_pruning:
        llm_critic_executed = True
        try:
            critic_result = await run_llm_critic_stage(
                reconciled=reconciled,
                source_url=classification.normalized_url,
                detected_type=classification.detected_type.value,
                providers=providers,
                initial_context=gathered_context,
                pipeline_outputs=pipeline_outputs_for_prompt,
                max_concurrency=orchestrator.max_concurrent_agents,
                cache=pipeline_cache,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("%s stage failed", STAGE_LLM_CRITIC)
            _append_unique_warning(warnings, f"llm_critic stage failed: {exc}")
        else:
            reconciled = critic_result.reconciled
            critic_pruned_excluded_entities = critic_result.pruned_excluded_entities
            logger.info(
                "%s: proposed_drop=%d applied_drop=%d protected_roots=%d",
                STAGE_LLM_CRITIC,
                critic_result.applied.get("proposed_drop_count", 0),
                critic_result.applied.get("applied_drop_count", 0),
                len(critic_result.applied.get("protected_root_ids", [])),
            )
            for warning in critic_result.warnings:
                _append_unique_warning(warnings, warning)

    strict_validation_entities = _iter_reconciled_entities(
        reconciled_entities=reconciled.entities,
        memberships=reconciled.memberships,
        contributions=reconciled.contributions,
    )
    strict_batch = StrictSchemaValidator().validate_batch(strict_validation_entities)
    logger.info(
        "%s: valid=%d invalid=%d",
        STAGE_STRICT_VALIDATION,
        len(strict_batch.valid_entities),
        len(strict_batch.invalid_entities),
    )
    for warning in strict_batch.warnings:
        _append_unique_warning(warnings, f"Strict validation: {warning}")

    jsonld_context = _extract_jsonld_context()
    try:
        assembled_output = assemble_output(
            reconciled,
            strict_batch,
            root_entity_type=_root_entity_type_for_detected_type(
                classification.detected_type.value,
            ),
        )
    except RootEntityValidationError as exc:
        failure_message = (
            f"Root {exc.entity_type} entity '{exc.entity_id}' failed strict validation"
        )
        logger.warning("%s: %s", STAGE_OUTPUT_ASSEMBLY, failure_message)
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
                for error in exc.validation_errors
            ],
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=error_payload.model_dump(mode="json", exclude_none=True),
        )
    except ValueError as exc:
        logger.warning("%s: rootless output — %s", STAGE_OUTPUT_ASSEMBLY, exc)
        assembled_output = _build_rootless_assembled_output(
            reconciled=reconciled,
            strict_batch=strict_batch,
            root_warning=str(exc),
        )

    if critic_pruned_excluded_entities:
        for excluded_entity in critic_pruned_excluded_entities:
            if not isinstance(excluded_entity, dict):
                continue
            assembled_output.excluded_entities.append(deepcopy(excluded_entity))
            entity_payload = excluded_entity.get("entity")
            entity_id = entity_payload.get("id") if isinstance(entity_payload, dict) else None
            assembled_output.warnings.append(
                (
                    f"Excluded {excluded_entity.get('entity_type', 'entity')} entity "
                    f"'{entity_id}' due to critic pruning"
                ),
            )

    for warning in assembled_output.warnings:
        _append_unique_warning(warnings, warning)

    entities_for_link_validation: list[dict[str, Any]] = []
    if isinstance(assembled_output.root_entity, dict):
        entities_for_link_validation.append(deepcopy(assembled_output.root_entity))
    entities_for_link_validation.extend(
        deepcopy(entity)
        for entity in assembled_output.related_entities
        if isinstance(entity, dict)
    )

    provider_cache = getattr(request.app.state, "v2_provider_cache", None)
    if not isinstance(provider_cache, ProviderCache):
        provider_cache = None

    link_veracity_started_at = perf_counter()
    try:
        link_veracity_result = await run_link_veracity_stage(
            entities=entities_for_link_validation,
            source_url=classification.normalized_url,
            providers=providers,
            max_concurrency=orchestrator.max_concurrent_agents,
            cache=provider_cache,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("%s stage failed", STAGE_LINK_VERACITY)
        _append_unique_warning(warnings, f"Link veracity stage failed: {exc}")
    else:
        logger.info(
            "%s: checked=%d supported=%d unsupported=%d failed=%d invalid_links=%d",
            STAGE_LINK_VERACITY,
            link_veracity_result.checked_count,
            link_veracity_result.supported_count,
            link_veracity_result.unsupported_count,
            link_veracity_result.failed_count,
            len(link_veracity_result.invalid_links),
        )
        _append_unique_warning(
            warnings,
            (
                "Link veracity summary: "
                f"checked={link_veracity_result.checked_count}, "
                f"supported={link_veracity_result.supported_count}, "
                f"unsupported={link_veracity_result.unsupported_count}, "
                f"failed={link_veracity_result.failed_count}"
            ),
        )
        for warning in link_veracity_result.warnings:
            _append_unique_warning(warnings, warning)

        invalid_links = set(link_veracity_result.invalid_links)
        if invalid_links:
            assembled_output, id_rewrites, promotion_warnings = promote_failed_id_entities(
                assembled=assembled_output,
                invalid_links=invalid_links,
            )
            for warning in promotion_warnings:
                _append_unique_warning(warnings, warning)
            if id_rewrites:
                logger.info(
                    "%s: promoted %d entity id(s) past failed url(s)",
                    STAGE_LINK_VERACITY,
                    len(id_rewrites),
                )
                # The rewritten entities now expose their new id; remove the
                # old urls from invalid_links so we don't also try to prune them.
                invalid_links = invalid_links - set(id_rewrites)
                # Rebuild entity_link_map with the new ids so downstream pruning
                # acts on the right entity references.
                rebuilt_entity_link_map: dict[str, list[str]] = {}
                for old_id, links in link_veracity_result.entity_link_map.items():
                    new_id = id_rewrites.get(old_id, old_id)
                    rebuilt_entity_link_map.setdefault(new_id, []).extend(links)
            else:
                rebuilt_entity_link_map = link_veracity_result.entity_link_map

            if invalid_links:
                assembled_output, link_pruning_warnings = apply_link_pruning_to_assembled_output(
                    assembled=assembled_output,
                    invalid_links=invalid_links,
                    entity_link_map=rebuilt_entity_link_map,
                    article_identifier_link_map=link_veracity_result.article_identifier_link_map,
                )
                for warning in link_pruning_warnings:
                    _append_unique_warning(warnings, warning)

        assembled_output, article_validation_warnings = validate_articles(
            assembled_output,
            veracity_records=link_veracity_result.records,
        )
        for warning in article_validation_warnings:
            _append_unique_warning(warnings, warning)

    link_veracity_seconds = perf_counter() - link_veracity_started_at

    assembled_output, ownership_warnings = validate_ownership(assembled_output)
    if ownership_warnings:
        logger.info(
            "ownership_check: dropped %d invalid pulse:owns entr%s",
            len(ownership_warnings),
            "y" if len(ownership_warnings) == 1 else "ies",
        )
    for warning in ownership_warnings:
        _append_unique_warning(warnings, warning)

    assembled_output, owner_inference_warnings = infer_owners(assembled_output)
    if owner_inference_warnings:
        logger.info(
            "owner_inference: stamped %d ownership relationship(s)",
            len(owner_inference_warnings),
        )
    for warning in owner_inference_warnings:
        _append_unique_warning(warnings, warning)

    assembled_output, org_unit_warnings = infer_org_units(assembled_output)
    if org_unit_warnings:
        logger.info(
            "org_unit_inference: stamped %d unit relationship(s)",
            len(org_unit_warnings),
        )
    for warning in org_unit_warnings:
        _append_unique_warning(warnings, warning)

    shacl_graph_payload = build_jsonld_output(
        assembled=assembled_output,
        jsonld_context=jsonld_context,
    )
    graph_nodes = shacl_graph_payload.get("@graph")
    logger.info(
        "%s: entities=%d context_terms=%d",
        STAGE_JSONLD_BUILD,
        len(graph_nodes) if isinstance(graph_nodes, list) else 0,
        len(jsonld_context),
    )

    shacl_data_graph = _jsonld_to_graph(shacl_graph_payload)
    if shacl_data_graph is None:
        logger.warning("%s: skipped — unable to parse assembled graph payload", STAGE_SHACL_GATE)
        _append_unique_warning(
            warnings,
            "SHACL validation skipped: unable to parse assembled graph payload",
        )
    else:
        try:
            shacl_result = SHACLValidator().validate_graph(
                shacl_data_graph,
                load_ontology_shapes_graph(),
            )
        except SHACLRuntimeUnavailableError as exc:
            logger.warning("%s: skipped — %s", STAGE_SHACL_GATE, exc)
            _append_unique_warning(warnings, str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("%s failed", STAGE_SHACL_GATE)
            _append_unique_warning(warnings, f"SHACL validation failed: {exc}")
        else:
            for violation in shacl_result.violations:
                _append_unique_warning(
                    warnings,
                    (
                        "SHACL violation: "
                        f"focus={violation.get('focusNode')}, "
                        f"path={violation.get('path')}, "
                        f"message={violation.get('message')}"
                    ),
                )
            for shacl_warning in shacl_result.warnings:
                _append_unique_warning(
                    warnings,
                    (
                        "SHACL warning: "
                        f"focus={shacl_warning.get('focusNode')}, "
                        f"path={shacl_warning.get('path')}, "
                        f"message={shacl_warning.get('message')}"
                    ),
                )
            logger.info(
                "%s: conforms=%s violations=%d warnings=%d",
                STAGE_SHACL_GATE,
                shacl_result.conforms,
                len(shacl_result.violations),
                len(shacl_result.warnings),
            )

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
    extract_graph = _jsonld_to_graph(output_payload) if output_format == "jsonld" else None
    final_entities = []
    if isinstance(assembled_output.root_entity, dict):
        final_entities.append(assembled_output.root_entity)
    final_entities.extend(assembled_output.related_entities)
    final_entity_count = len(final_entities)

    completed_stages = list(pipeline_result.stages_completed)
    stage_sequence = [STAGE_PERMISSIVE_VALIDATION]
    if llm_dedup_executed:
        stage_sequence.append(STAGE_LLM_DEDUP)
    stage_sequence.append(STAGE_RECONCILIATION)
    if llm_critic_executed:
        stage_sequence.append(STAGE_LLM_CRITIC)
    stage_sequence.extend(
        [
            STAGE_STRICT_VALIDATION,
            STAGE_OUTPUT_ASSEMBLY,
            STAGE_LINK_VERACITY,
            STAGE_JSONLD_BUILD,
            STAGE_SHACL_GATE,
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
        _format_duration(total_seconds),
        _format_duration(orchestrator_seconds),
        _format_duration(link_veracity_seconds),
        _format_duration(other_seconds),
    )

    response_model = V2ExtractResponse(
        source_url=classification.normalized_url,
        detected_type=classification.detected_type.value,
        output_format=output_format,
        output=response_output,
        context_summary_markdown=context_summary_markdown,
        warnings=warnings,
        stats=stats,
    )

    if pipeline_cache is not None and pipeline_cache_key is not None:
        try:
            pipeline_cache.set(
                pipeline_cache_key,
                response_model.model_dump(mode="json", exclude_none=True),
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "failed to write pipeline cache (run_id=%s)",
                run_id,
            )

    written_log_path = query_log.write()
    if written_log_path is not None:
        logger.info("query log written: %s", written_log_path)

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
) -> V2ExtractJobAccepted | JSONResponse:
    """Submit an extraction asynchronously. Returns a job id to poll via GET."""

    try:
        classification = classify_github_url(payload.source_url)
    except UnsupportedGitHubURL as exc:
        error_payload = V2ErrorResponse(
            error_type=V2ErrorType.UNSUPPORTED_URL,
            detail=exc.reason,
            source_url=exc.normalized_url,
            detected_path_kind=_extract_path_kind(payload.source_url),
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
            detected_path_kind=_extract_path_kind(payload.source_url),
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=error_payload.model_dump(mode="json", exclude_none=True),
        )

    job_store = _resolve_job_store(request)
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
    _track_background_task(request, task)

    logger.info(
        "extract job submitted: job_id=%s url=%s detected_type=%s",
        job_id,
        classification.normalized_url,
        classification.detected_type.value,
    )
    return V2ExtractJobAccepted(
        job_id=job_id,
        status=V2ExtractJobStatus.PENDING,
        status_url=_job_status_path(job_id),
        submitted_at=submitted_at,
    )


@v2_router.get(
    "/jobs/{job_id}",
    response_model=V2ExtractJob,
    response_model_exclude_none=True,
)
async def extract_job(
    job_id: Annotated[str, Path(description="Job id returned by POST /v2/extract.")],
    request: Request,
) -> V2ExtractJob | JSONResponse:
    """Retrieve a previously submitted extraction job by id."""

    job_store = _resolve_job_store(request)
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
    return record


@v2_router.get(
    "/health",
    response_model=V2HealthResponse,
)
async def health() -> V2HealthResponse:
    component_statuses: dict[str, Literal["healthy", "degraded", "unhealthy"]] = {
        "python": (
            "healthy"
            if sys.version_info[:2] >= MIN_SUPPORTED_PYTHON
            else "unhealthy"
        ),
    }

    config: V2Config | None = None
    try:
        config = V2Config()
        component_statuses["config"] = "healthy"
    except ValueError:
        component_statuses["config"] = "unhealthy"

    component_statuses["github_token"] = (
        "healthy" if config and config.GITHUB_TOKEN else "degraded"
    )

    overall_status: Literal["healthy", "degraded", "unhealthy"]
    if "unhealthy" in component_statuses.values():
        overall_status = "unhealthy"
    elif "degraded" in component_statuses.values():
        overall_status = "degraded"
    else:
        overall_status = "healthy"

    return V2HealthResponse(
        status=overall_status,
        components=component_statuses,
        version=PACKAGE_VERSION,
    )
