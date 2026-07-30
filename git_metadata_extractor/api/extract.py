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

from git_metadata_extractor.agents import AgentRuntime, ProviderSet, parse_agent_runtime
from git_metadata_extractor.agents.llm.refiners.ror_parent.agent import RorParentSelectorAgent
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
from git_metadata_extractor.pipeline.stages import (
    AssembledOutput,
    RootEntityValidationError,
    apply_link_pruning_to_assembled_output,
    assemble_output,
    build_json_output,
    build_jsonld_output,
    compute_stats,
    demote_github_props_to_units,
    emit_fork_parent_stubs,
    guarantee_repo_author,
    infer_article_source_organization,
    infer_github_handle_parents,
    infer_org_units,
    infer_owners,
    promote_failed_id_entities,
    prune_dangling_refs,
    reconcile_entities,
    run_concept_tagging_stage,
    run_link_veracity_stage,
    run_llm_critic_stage,
    run_llm_dedup_stage,
    run_org_relationships_stage,
    run_refine_with_llm_stage,
    run_resolve_bio_to_ror_llm_stage,
    run_resolve_bio_to_ror_stage,
    run_resolve_company_to_ror_stage,
    run_resolve_placeholder_orgs_to_ror_stage,
    tag_rule_based_disciplines,
    validate_articles,
    validate_author_classes,
    validate_ownership,
)
from git_metadata_extractor.pipeline.stages.concept_tagging import (
    is_enabled as _concept_tagging_is_enabled,
)
from git_metadata_extractor.pipeline.stages.concept_tagging import (
    resolve_backend as _resolve_concept_tagging_backend,
)
from git_metadata_extractor.pipeline.stages.concept_tagging import (
    resolve_epfl_min_score as _resolve_concept_tagging_epfl_min_score,
)
from git_metadata_extractor.pipeline.stages.concept_tagging import (
    resolve_related_enrichment as _resolve_concept_tagging_related_enrichment,
)
from git_metadata_extractor.pipeline.stages.context_gather import RequiredProviderUnavailableError
from git_metadata_extractor.pipeline.stages.refine_with_llm import (
    is_enabled as _hybrid_refiner_is_enabled,
)
from git_metadata_extractor.pipeline.stages.validate_org_github_handles import (
    validate_org_github_handles,
)
from git_metadata_extractor.schema import load_jsonld_context
from git_metadata_extractor.validation import (
    SHACLValidator,
    StrictSchemaValidator,
    load_ontology_shapes_graph,
)
from git_metadata_extractor.validation.shacl_validation import SHACLRuntimeUnavailableError

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
    permissive_entity_count = sum(len(bucket) for bucket in typed_entity_buckets.values())
    logger.info("%s: entity_count=%d", _helpers.STAGE_PERMISSIVE_VALIDATION, permissive_entity_count)

    llm_dedup_executed = False
    if resolved_runtime == AgentRuntime.LLM:
        llm_dedup_executed = True
        stage_started_at = perf_counter()
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
        except Exception as exc:
            logger.exception("%s stage failed", _helpers.STAGE_LLM_DEDUP)
            _append_unique_warning(warnings, f"llm_dedup stage failed: {exc}")
        else:
            typed_entity_buckets = dedup_result.typed_entity_buckets
            logger.info(
                "%s: accepted=%d rejected=%d remap=%d in %.2fs",
                _helpers.STAGE_LLM_DEDUP,
                dedup_result.accepted_cluster_count,
                dedup_result.rejected_cluster_count,
                dedup_result.remap_count,
                perf_counter() - stage_started_at,
            )
            for warning in dedup_result.warnings:
                _append_unique_warning(warnings, warning)

    llm_critic_executed = False
    critic_pruned_excluded_entities: list[dict[str, Any]] = []
    stage_started_at = perf_counter()
    reconciled = reconcile_entities(typed_entity_buckets)
    logger.info(
        "%s: persons=%d orgs=%d repos=%d articles=%d memberships=%d contributions=%d in %.2fs",
        _helpers.STAGE_RECONCILIATION,
        len(reconciled.entities.get("persons", [])),
        len(reconciled.entities.get("organizations", [])),
        len(reconciled.entities.get("repositories", [])),
        len(reconciled.entities.get("articles", [])),
        len(reconciled.memberships),
        len(reconciled.contributions),
        perf_counter() - stage_started_at,
    )
    for warning in reconciled.link_warnings:
        _append_unique_warning(warnings, warning)

    # === resolve_company_to_ror stage ============================
    # Stamp `schema:affiliation` on persons whose `gme-internal:company`
    # string resolves confidently against the ROR RAG. Runs after
    # reconciliation so we don't touch entities the critic would later
    # drop, but before the LLM critic / refiner — that way the critic
    # sees the resolved affiliations and refine_with_llm doesn't waste
    # cycles re-resolving the same companies via its rescue path.
    if _helpers._resolve_company_to_ror_enabled():
        stage_started_at = perf_counter()
        try:
            company_result = await run_resolve_company_to_ror_stage(
                reconciled=reconciled,
                provider=getattr(providers, "ror_rag", None),
                github_provider=getattr(providers, "github", None),
            )
            logger.info(
                "%s: persons_examined=%d persons_resolved=%d "
                "memberships=%d organizations=%d "
                "queries=%d accepted=%d in %.2fs",
                _helpers.STAGE_RESOLVE_COMPANY_TO_ROR,
                company_result.persons_examined,
                company_result.persons_resolved,
                company_result.memberships_created,
                company_result.organizations_created,
                company_result.queries_attempted,
                company_result.queries_accepted,
                perf_counter() - stage_started_at,
            )
        except Exception as exc:
            logger.exception("%s stage failed", _helpers.STAGE_RESOLVE_COMPANY_TO_ROR)
            _append_unique_warning(
                warnings,
                f"resolve_company_to_ror stage failed: {exc}",
            )

    # === resolve_bio_to_ror stage ================================
    # Backstop for persons the company-string stage missed: pulls
    # affiliation hints from `_bio` / `_orcid_biography` / `_blog`.
    # Runs in the same slot as company-resolution (after reconciliation,
    # before critic) and reuses the same strict acceptance gate, so its
    # output looks identical to the critic / refiner — they see the same
    # `schema:affiliation` triples regardless of which extractor stamped
    # them.
    if _helpers._resolve_bio_to_ror_enabled():
        stage_started_at = perf_counter()
        try:
            bio_result = await run_resolve_bio_to_ror_stage(
                reconciled=reconciled,
                provider=getattr(providers, "ror_rag", None),
            )
            logger.info(
                "%s: persons_examined=%d persons_resolved=%d "
                "memberships=%d organizations=%d "
                "candidates=%d queries=%d accepted=%d in %.2fs",
                _helpers.STAGE_RESOLVE_BIO_TO_ROR,
                bio_result.persons_examined,
                bio_result.persons_resolved,
                bio_result.memberships_created,
                bio_result.organizations_created,
                bio_result.candidates_extracted,
                bio_result.queries_attempted,
                bio_result.queries_accepted,
                perf_counter() - stage_started_at,
            )
        except Exception as exc:
            logger.exception("%s stage failed", _helpers.STAGE_RESOLVE_BIO_TO_ROR)
            _append_unique_warning(
                warnings,
                f"resolve_bio_to_ror stage failed: {exc}",
            )

    # === resolve_bio_to_ror_llm stage (LLM / hybrid only) =========
    # One LLM call per person STILL missing `schema:affiliation` after
    # Stage A. Costly enough that it's gated behind the LLM runtime —
    # under rule-based it never runs even when the flag is on. The
    # caller-side acceptance gate (confidence >= 0.7, verbatim quote
    # in `reason`) matches the system prompt; the agent additionally
    # blanks out the ROR field below that floor before the patch
    # reaches this stage.
    if (
        resolved_runtime in (AgentRuntime.LLM, AgentRuntime.HYBRID)
        and _helpers._resolve_bio_to_ror_llm_enabled()
    ):
        stage_started_at = perf_counter()
        try:
            bio_llm_result = await run_resolve_bio_to_ror_llm_stage(
                reconciled=reconciled,
                provider=getattr(providers, "ror_rag", None),
            )
            logger.info(
                "%s: persons_examined=%d called=%d resolved=%d failed=%d "
                "memberships=%d organizations=%d in %.2fs",
                _helpers.STAGE_RESOLVE_BIO_TO_ROR_LLM,
                bio_llm_result.persons_examined,
                bio_llm_result.persons_called,
                bio_llm_result.persons_resolved,
                bio_llm_result.persons_failed,
                bio_llm_result.memberships_created,
                bio_llm_result.organizations_created,
                perf_counter() - stage_started_at,
            )
            for warning in bio_llm_result.warnings:
                _append_unique_warning(warnings, warning)
        except Exception as exc:
            logger.exception("%s stage failed", _helpers.STAGE_RESOLVE_BIO_TO_ROR_LLM)
            _append_unique_warning(
                warnings,
                f"resolve_bio_to_ror_llm stage failed: {exc}",
            )

    # === resolve_placeholder_orgs_to_ror stage ====================
    # Late pass that re-queries the ROR RAG against the `schema:name`
    # of every `idSource == "uuid"` Organization (the breadcrumb
    # carried by rescue / fallback paths that minted a placeholder
    # Org without a registry id). Rewrites successful hits in place
    # and patches every referring Membership composite. Runs after
    # the three Person-side resolvers because it's strictly weaker —
    # those stages have richer signal (`_company`, `_bio`, etc.) and
    # should win first.
    if _helpers._resolve_placeholder_orgs_to_ror_enabled():
        stage_started_at = perf_counter()
        try:
            placeholder_result = await run_resolve_placeholder_orgs_to_ror_stage(
                reconciled=reconciled,
                provider=getattr(providers, "ror_rag", None),
            )
            logger.info(
                "%s: examined=%d resolved=%d memberships_rewritten=%d "
                "queries=%d accepted=%d in %.2fs",
                _helpers.STAGE_RESOLVE_PLACEHOLDER_ORGS_TO_ROR,
                placeholder_result.placeholders_examined,
                placeholder_result.placeholders_resolved,
                placeholder_result.memberships_rewritten,
                placeholder_result.queries_attempted,
                placeholder_result.queries_accepted,
                perf_counter() - stage_started_at,
            )
        except Exception as exc:
            logger.exception(
                "%s stage failed", _helpers.STAGE_RESOLVE_PLACEHOLDER_ORGS_TO_ROR,
            )
            _append_unique_warning(
                warnings,
                f"resolve_placeholder_orgs_to_ror stage failed: {exc}",
            )

    apply_critic_pruning = _helpers._should_apply_critic_pruning()
    if resolved_runtime == AgentRuntime.LLM and not apply_critic_pruning:
        logger.info(
            "%s: skipped (V2_APPLY_CRITIC_PRUNING=false — entities preserved)",
            _helpers.STAGE_LLM_CRITIC,
        )
    if resolved_runtime == AgentRuntime.LLM and apply_critic_pruning:
        llm_critic_executed = True
        stage_started_at = perf_counter()
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
        except Exception as exc:
            logger.exception("%s stage failed", _helpers.STAGE_LLM_CRITIC)
            _append_unique_warning(warnings, f"llm_critic stage failed: {exc}")
        else:
            reconciled = critic_result.reconciled
            critic_pruned_excluded_entities = critic_result.pruned_excluded_entities
            logger.info(
                "%s: proposed_drop=%d applied_drop=%d protected_roots=%d in %.2fs",
                _helpers.STAGE_LLM_CRITIC,
                critic_result.applied.get("proposed_drop_count", 0),
                critic_result.applied.get("applied_drop_count", 0),
                len(critic_result.applied.get("protected_root_ids", [])),
                perf_counter() - stage_started_at,
            )
            for warning in critic_result.warnings:
                _append_unique_warning(warnings, warning)

    if resolved_runtime == AgentRuntime.HYBRID and _hybrid_refiner_is_enabled():
        stage_started_at = perf_counter()
        try:
            refine_result = await run_refine_with_llm_stage(
                reconciled=reconciled,
                gathered_context=gathered_context,
                epfl_graph_provider=providers.epfl_graph_rag,
                providers=providers,
                max_concurrency=orchestrator.max_concurrent_agents,
            )
        except Exception as exc:
            logger.exception("%s stage failed", _helpers.STAGE_REFINE_WITH_LLM)
            _append_unique_warning(
                warnings,
                f"refine_with_llm stage failed: {exc}",
            )
        else:
            reconciled = refine_result.reconciled
            logger.info(
                "%s: refined=%d skipped=%d failed=%d in %.2fs",
                _helpers.STAGE_REFINE_WITH_LLM,
                refine_result.refined_count,
                refine_result.skipped_count,
                refine_result.failed_count,
                perf_counter() - stage_started_at,
            )
            for warning in refine_result.warnings:
                _append_unique_warning(warnings, warning)

    # KNOWN BUG salvage: when reconciliation drops unresolvable
    # `schema:author` references, a repository can end up with an empty
    # author array, which strict validation rejects (schema requires
    # non-empty). Fall back to the github owner if it's in the graph.
    stage_started_at = perf_counter()
    reconciled, repo_author_warnings = guarantee_repo_author(reconciled)
    logger.info(
        "guarantee_repo_author: salvaged=%d in %.2fs",
        len(repo_author_warnings),
        perf_counter() - stage_started_at,
    )
    for warning in repo_author_warnings:
        _append_unique_warning(warnings, warning)

    # Validate `@handle`-style org names against GitHub before strict
    # validation so we either stamp the missing handle or drop the
    # hallucinated entity (rather than just losing it to anyOf).
    stage_started_at = perf_counter()
    reconciled, org_handle_warnings = validate_org_github_handles(reconciled, providers)
    logger.info(
        "validate_org_github_handles: actions=%d in %.2fs",
        len(org_handle_warnings),
        perf_counter() - stage_started_at,
    )
    for warning in org_handle_warnings:
        _append_unique_warning(warnings, warning)

    stage_started_at = perf_counter()
    strict_validation_entities = _iter_reconciled_entities(
        reconciled_entities=reconciled.entities,
        memberships=reconciled.memberships,
        contributions=reconciled.contributions,
    )
    strict_batch = StrictSchemaValidator().validate_batch(strict_validation_entities)
    logger.info(
        "%s: valid=%d invalid=%d in %.2fs",
        _helpers.STAGE_STRICT_VALIDATION,
        len(strict_batch.valid_entities),
        len(strict_batch.invalid_entities),
        perf_counter() - stage_started_at,
    )
    for warning in strict_batch.warnings:
        _append_unique_warning(warnings, f"Strict validation: {warning}")

    jsonld_context = _extract_jsonld_context()
    stage_started_at = perf_counter()
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
                for error in exc.validation_errors
            ],
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=error_payload.model_dump(mode="json", exclude_none=True),
        )
    except ValueError as exc:
        logger.warning("%s: rootless output — %s", _helpers.STAGE_OUTPUT_ASSEMBLY, exc)
        assembled_output = _build_rootless_assembled_output(
            reconciled=reconciled,
            strict_batch=strict_batch,
            root_warning=str(exc),
        )
    logger.info(
        "%s: related=%d excluded=%d warnings=%d in %.2fs",
        _helpers.STAGE_OUTPUT_ASSEMBLY,
        len(assembled_output.related_entities),
        len(assembled_output.excluded_entities),
        len(assembled_output.warnings),
        perf_counter() - stage_started_at,
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
    link_veracity_result = None
    if resolved_runtime != AgentRuntime.LLM:
        # link_veracity calls an LLM per link, so it has no place in
        # `agent_runtime=rule_based` (the whole point of rule-based mode is
        # zero LLM calls). The `V2_LINK_VERACITY_ENABLED` env var still
        # gates the stage *within* LLM mode for users who want fast LLM
        # extracts without per-link verification.
        logger.info(
            "%s: skipped (agent_runtime=%s — link veracity is LLM-only)",
            _helpers.STAGE_LINK_VERACITY,
            resolved_runtime.value,
        )
    elif not _helpers._is_link_veracity_enabled():
        logger.info(
            "%s: skipped (V2_LINK_VERACITY_ENABLED=false)",
            _helpers.STAGE_LINK_VERACITY,
        )
    else:
        try:
            link_veracity_result = await run_link_veracity_stage(
                entities=entities_for_link_validation,
                source_url=classification.normalized_url,
                providers=providers,
                max_concurrency=orchestrator.max_concurrent_agents,
                cache=provider_cache,
            )
        except Exception as exc:
            logger.exception("%s stage failed", _helpers.STAGE_LINK_VERACITY)
            _append_unique_warning(warnings, f"Link veracity stage failed: {exc}")
        else:
            logger.info(
                "%s: checked=%d supported=%d unsupported=%d failed=%d invalid_links=%d in %.2fs",
                _helpers.STAGE_LINK_VERACITY,
                link_veracity_result.checked_count,
                link_veracity_result.supported_count,
                link_veracity_result.unsupported_count,
                link_veracity_result.failed_count,
                len(link_veracity_result.invalid_links),
                perf_counter() - link_veracity_started_at,
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
                        _helpers.STAGE_LINK_VERACITY,
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

    # Article-validation runs whether or not link-veracity ran. If
    # link-veracity was skipped, supported/unsupported sets will be empty,
    # and the stage will only drop articles with placeholder/sentinel DOIs
    # (i.e. its non-veracity-dependent rules still apply).
    veracity_records = (
        link_veracity_result.records if link_veracity_result is not None else []
    )
    stage_started_at = perf_counter()
    assembled_output, article_validation_warnings = validate_articles(
        assembled_output,
        veracity_records=veracity_records,
    )
    logger.info(
        "validate_articles: warnings=%d in %.2fs",
        len(article_validation_warnings),
        perf_counter() - stage_started_at,
    )
    for warning in article_validation_warnings:
        _append_unique_warning(warnings, warning)

    stage_started_at = perf_counter()
    assembled_output, author_class_warnings = validate_author_classes(assembled_output)
    logger.info(
        "author_class_validation: pruned=%d in %.2fs",
        len(author_class_warnings),
        perf_counter() - stage_started_at,
    )
    for warning in author_class_warnings:
        _append_unique_warning(warnings, warning)

    link_veracity_seconds = perf_counter() - link_veracity_started_at

    stage_started_at = perf_counter()
    assembled_output, ownership_warnings = validate_ownership(assembled_output)
    logger.info(
        "ownership_check: dropped=%d in %.2fs",
        len(ownership_warnings),
        perf_counter() - stage_started_at,
    )
    for warning in ownership_warnings:
        _append_unique_warning(warnings, warning)

    # Run `infer_owners` BEFORE `prune_dangling_refs` so it can materialise
    # minimal Person stubs for github owners that have no matching entity
    # (otherwise prune would clear `pulse:ownedBy` first and the stub
    # opportunity is lost).
    stage_started_at = perf_counter()
    assembled_output, owner_inference_warnings = infer_owners(assembled_output)
    logger.info(
        "owner_inference: stamped=%d in %.2fs",
        len(owner_inference_warnings),
        perf_counter() - stage_started_at,
    )
    for warning in owner_inference_warnings:
        _append_unique_warning(warnings, warning)

    # Second-pass inverse consistency check — `infer_owners` indexes by
    # github handle and may have just stamped `pulse:owns: [repo]` on
    # the ROR-side Org instead of (or in addition to) the github-handle
    # Org. Re-run `validate_ownership` so its dual-identity guard drops
    # entries on whichever Org doesn't match the repo's actual
    # `pulse:ownedBy`. Production audit (Bug J) showed this is the
    # most common path to broken inverses (165 cases on ENAC-CNPA et al.).
    stage_started_at = perf_counter()
    assembled_output, inverse_warnings = validate_ownership(assembled_output)
    logger.info(
        "inverse_consistency: dropped=%d in %.2fs",
        len(inverse_warnings),
        perf_counter() - stage_started_at,
    )
    for warning in inverse_warnings:
        _append_unique_warning(warnings, warning)

    stage_started_at = perf_counter()
    assembled_output, prune_warnings = prune_dangling_refs(assembled_output)
    logger.info(
        "prune_dangling_refs: actions=%d in %.2fs",
        len(prune_warnings),
        perf_counter() - stage_started_at,
    )
    for warning in prune_warnings:
        _append_unique_warning(warnings, warning)

    # Fuzzy-search ROR for parent organizations of every github-only org in
    # the graph. The github org always remains as a standalone entity; ROR
    # matches get added as additional org entities and the best match
    # becomes the github org's `unitOf` parent.
    # Runs before the LLM relationship stage so it sees the new ROR entities
    # and can refine the unitOf decision; runs before `infer_org_units` so
    # the token-overlap fallback also gets the broader graph.
    stage_started_at = perf_counter()
    # In LLM / hybrid runtimes an LLM agent picks the single correct ROR
    # parent (or declines) from the fuzzy-search candidates. In rule_based
    # runtime there is no selector — `infer_github_handle_parents` falls back
    # to a strict deterministic rule, keeping that mode LLM-free.
    ror_parent_selector = (
        RorParentSelectorAgent()
        if resolved_runtime != AgentRuntime.RULE_BASED
        else None
    )
    assembled_output, github_parent_warnings = await infer_github_handle_parents(
        assembled_output,
        providers=providers,
        parent_selector=ror_parent_selector,
    )
    logger.info(
        "github_handle_parents: actions=%d in %.2fs",
        len(github_parent_warnings),
        perf_counter() - stage_started_at,
    )
    for warning in github_parent_warnings:
        _append_unique_warning(warnings, warning)

    if resolved_runtime == AgentRuntime.LLM:
        stage_started_at = perf_counter()
        try:
            assembled_output, org_relationship_warnings = await run_org_relationships_stage(
                assembled=assembled_output,
                source_url=classification.normalized_url,
                providers=providers,
            )
        except Exception as exc:
            logger.exception("org_relationships stage failed")
            _append_unique_warning(warnings, f"org_relationships stage failed: {exc}")
        else:
            logger.info(
                "org_relationships: edges=%d in %.2fs",
                len(org_relationship_warnings),
                perf_counter() - stage_started_at,
            )
            for warning in org_relationship_warnings:
                _append_unique_warning(warnings, warning)

    stage_started_at = perf_counter()
    assembled_output, org_unit_warnings = infer_org_units(assembled_output)
    logger.info(
        "org_unit_inference: stamped=%d in %.2fs",
        len(org_unit_warnings),
        perf_counter() - stage_started_at,
    )
    for warning in org_unit_warnings:
        _append_unique_warning(warnings, warning)

    stage_started_at = perf_counter()
    assembled_output, demote_warnings = demote_github_props_to_units(assembled_output)
    logger.info(
        "demote_github_props_to_units: demoted=%d in %.2fs",
        len(demote_warnings),
        perf_counter() - stage_started_at,
    )
    for warning in demote_warnings:
        _append_unique_warning(warnings, warning)

    # Emit minimal stubs for fork parents so `pulse:isForkOf` references
    # satisfy SHACL `sh:class schema:SoftwareSourceCode` without forcing
    # us to ingest the upstream repo.
    stage_started_at = perf_counter()
    assembled_output, fork_stub_warnings = emit_fork_parent_stubs(assembled_output)
    logger.info(
        "fork_parent_stubs: emitted=%d in %.2fs",
        len(fork_stub_warnings),
        perf_counter() - stage_started_at,
    )
    for warning in fork_stub_warnings:
        _append_unique_warning(warnings, warning)

    # Deterministic inference: stamp `schema:sourceOrganization` on
    # Articles that don't have one when there is exactly one Org an
    # author was a confirmed member of on the article's publication
    # date. Refuses to guess when the answer is ambiguous.
    stage_started_at = perf_counter()
    assembled_output, source_org_warnings = infer_article_source_organization(assembled_output)
    logger.info(
        "article_source_org_inference: stamped=%d in %.2fs",
        len(source_org_warnings),
        perf_counter() - stage_started_at,
    )
    for warning in source_org_warnings:
        _append_unique_warning(warnings, warning)

    # Deterministic discipline tagging via the EPFL Graph disciplines
    # Qdrant RAG. Runs only when the root is a repository and the field
    # is still empty (does not overwrite upstream agent output). Result
    # is gated on the SHACL DisciplineEnumeration so output is always
    # schema-valid.
    if classification.detected_type.value == "repository":
        stage_started_at = perf_counter()
        readme_text_for_disciplines: str | None = None
        github_description_for_disciplines: str | None = None
        repository_context = (
            gathered_context.get("repository")
            if isinstance(gathered_context, dict)
            else None
        )
        if isinstance(repository_context, dict):
            candidate = repository_context.get("readme_content")
            if isinstance(candidate, str):
                readme_text_for_disciplines = candidate
            # The GitHub REST `description` field carries the repo's one-line
            # pitch, which is far more discriminative for discipline matching
            # than the first 4k of the README (often HTML/badge soup).
            metadata = repository_context.get("metadata")
            if isinstance(metadata, dict):
                gh_desc = metadata.get("description")
                if isinstance(gh_desc, str) and gh_desc.strip():
                    github_description_for_disciplines = gh_desc.strip()
        assembled_output, discipline_warnings = await tag_rule_based_disciplines(
            assembled_output,
            readme_text=readme_text_for_disciplines,
            github_description=github_description_for_disciplines,
        )
        logger.info(
            "rule_based_disciplines: emitted=%d in %.2fs",
            sum(1 for w in discipline_warnings if "Inferred pulse:discipline" in w),
            perf_counter() - stage_started_at,
        )
        for warning in discipline_warnings:
            _append_unique_warning(warnings, warning)

    if _concept_tagging_is_enabled() and classification.detected_type.value == "repository":
        repository_context = (
            gathered_context.get("repository")
            if isinstance(gathered_context, dict)
            else None
        )
        readme_text = (
            repository_context.get("readme_content")
            if isinstance(repository_context, dict)
            else None
        )
        backend = _resolve_concept_tagging_backend()
        stage_started_at = perf_counter()
        try:
            tagged_root, tagging_result = await run_concept_tagging_stage(
                root_entity=assembled_output.root_entity,
                readme_text=readme_text,
                backend=backend,
                epfl_min_score=_resolve_concept_tagging_epfl_min_score(),
                enable_related_openalex=_resolve_concept_tagging_related_enrichment(),
            )
        except Exception as exc:
            logger.exception("concept_tagging stage failed")
            _append_unique_warning(warnings, f"concept_tagging stage failed: {exc}")
        else:
            assembled_output.root_entity = tagged_root
            logger.info(
                "concept_tagging: backend=%s keywords=%d concepts=%d disciplines=%d in %.2fs",
                tagging_result.backend,
                len(tagging_result.keywords),
                len(tagging_result.concepts),
                len(tagging_result.disciplines),
                perf_counter() - stage_started_at,
            )
            for warning in tagging_result.warnings:
                _append_unique_warning(warnings, warning)

    stage_started_at = perf_counter()
    shacl_graph_payload = build_jsonld_output(
        assembled=assembled_output,
        jsonld_context=jsonld_context,
        include_internal_fields=include_internal_fields,
    )
    graph_nodes = shacl_graph_payload.get("@graph")
    logger.info(
        "%s: entities=%d context_terms=%d in %.2fs",
        _helpers.STAGE_JSONLD_BUILD,
        len(graph_nodes) if isinstance(graph_nodes, list) else 0,
        len(jsonld_context),
        perf_counter() - stage_started_at,
    )

    shacl_data_graph = _helpers._jsonld_to_graph(shacl_graph_payload)
    if shacl_data_graph is None:
        logger.warning("%s: skipped — unable to parse assembled graph payload", _helpers.STAGE_SHACL_GATE)
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
            logger.warning("%s: skipped — %s", _helpers.STAGE_SHACL_GATE, exc)
            _append_unique_warning(warnings, str(exc))
        except Exception as exc:
            logger.exception("%s failed", _helpers.STAGE_SHACL_GATE)
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
                _helpers.STAGE_SHACL_GATE,
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
    auto_ingest._maybe_schedule_huggingface_papers_auto_ingest(
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

