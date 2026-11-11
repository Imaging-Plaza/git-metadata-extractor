"""Shared extraction use case. No HTTP requests, responses or route dependencies."""

from __future__ import annotations

import logging
from copy import deepcopy
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Literal
from uuid import uuid4

from git_metadata_extractor import config as runtime_config
from git_metadata_extractor.agents import ProviderSet, parse_agent_runtime
from git_metadata_extractor.agents.tool_catalog import current_tool_fingerprint
from git_metadata_extractor.api_models import (
    V2ErrorResponse,
    V2ErrorType,
    V2ExtractResponse,
    V2FieldError,
    V2JSONLDOutput,
    V2JSONOutputEnvelope,
)
from git_metadata_extractor.config import V2Config
from git_metadata_extractor.mapping import mapping_manifest
from git_metadata_extractor.models import InternalModel
from git_metadata_extractor.observation.evidence import VERSION as EVIDENCE_VERSION
from git_metadata_extractor.observation.evidence import observe_providers
from git_metadata_extractor.observation.query_log import QueryLog, query_log_var
from git_metadata_extractor.pipeline import PipelineOrchestrator, settings
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
from git_metadata_extractor.pipeline.settings import JSONLD_CONTEXT_FALLBACK
from git_metadata_extractor.pipeline.stages import (
    RootEntityValidationError,
    build_json_output,
    compute_stats,
)
from git_metadata_extractor.pipeline.stages.context_gather import (
    RequiredProviderUnavailableError,
)
from git_metadata_extractor.pipeline.state import PipelineState
from git_metadata_extractor.providers.cache import ProviderCache, cache_refresh_active
from git_metadata_extractor.providers.detection import (
    UnsupportedGitHubURL,
    classify_github_url,
)
from git_metadata_extractor.providers.detection.models import GitHubURLClassification
from git_metadata_extractor.providers.sources_service import (
    current_sources_fingerprint,
    sources_scope,
)
from git_metadata_extractor.schema import load_jsonld_context

logger = logging.getLogger(__name__)


class ExtractionOptions(InternalModel, frozen=True):
    source_url: str
    agent_runtime: Literal["rule_based", "llm", "hybrid"] | None = None
    output_format: Literal["jsonld", "json"] = "jsonld"
    include_context_summary: bool = False
    include_internal_fields: bool = False
    read_only: bool = False


class ExtractionResult(InternalModel, frozen=True):
    response: V2ExtractResponse
    run_id: str
    cache_hit: bool
    classification: GitHubURLClassification


class ExtractionError(Exception):
    def __init__(self, error: V2ErrorResponse):
        super().__init__(error.detail)
        self.error = error


async def execute_extraction(  # noqa: PLR0913 - explicit dependencies
    options: ExtractionOptions,
    *,
    providers: ProviderSet,
    orchestrator: PipelineOrchestrator | None = None,
    cache: ProviderCache | None = None,
    execution_profile: dict | None = None,
    classification: GitHubURLClassification | None = None,
) -> ExtractionResult:
    token = query_log_var.set(None)
    try:
        with sources_scope(providers.sources):
            return await _execute(
                options,
                providers=providers,
                orchestrator=orchestrator,
                cache=cache,
                execution_profile=execution_profile,
                classification=classification,
            )
    finally:
        query_log_var.reset(token)


def _extract_jsonld_context() -> dict[str, Any]:
    try:
        return load_jsonld_context()
    except (OSError, TypeError, ValueError):
        return dict(JSONLD_CONTEXT_FALLBACK)


async def _execute(  # noqa: C901, PLR0912, PLR0913, PLR0915 - preserve relocated stage sequence
    options, *, providers, orchestrator, cache, execution_profile, classification,
):
    full_path = options.source_url
    agent_runtime = options.agent_runtime
    output_format = options.output_format
    include_context_summary = options.include_context_summary
    include_internal_fields = options.include_internal_fields
    config = V2Config()
    run_id: str | None = None

    try:
        classification = classification or classify_github_url(full_path)
    except UnsupportedGitHubURL as exc:
        logger.warning(
            "classify_url: unsupported url=%s reason=%s", exc.normalized_url, exc.reason,
        )
        error_payload = V2ErrorResponse(
            error_type=V2ErrorType.UNSUPPORTED_URL,
            detail=exc.reason,
            source_url=exc.normalized_url,
            detected_path_kind=settings.extract_path_kind(full_path),
        )
        raise ExtractionError(error_payload) from exc
    except ValueError as exc:
        logger.warning("classify_url: invalid input %s: %s", full_path, exc)
        error_payload = V2ErrorResponse(
            error_type=V2ErrorType.UNSUPPORTED_URL,
            detail=str(exc),
            source_url=full_path,
            detected_path_kind=settings.extract_path_kind(full_path),
        )
        raise ExtractionError(error_payload) from exc

    run_id = str(uuid4())
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
    providers = observe_providers(providers)

    # Resolve the cache singleton once. Sub-stages always receive it (so
    # provider caches, agent verdicts, Selenium fetches, link-veracity, and
    # DuckDuckGo searches all keep working). The pipeline-level lookup/store
    # is gated separately by V2_PIPELINE_CACHE_ENABLED so it can be toggled
    # off without disabling the sub-caches.
    pipeline_cache = cache
    if not isinstance(pipeline_cache, ProviderCache):
        pipeline_cache = None
    pipeline_cache_enabled = (
        pipeline_cache is not None and settings.is_pipeline_cache_enabled()
    )
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
            evidence_version=EVIDENCE_VERSION,
            canonical_output=settings.canonical_output_enabled(),
            substrate_enabled=runtime_config.substrate_enabled(),
            execution_profile=(execution_profile or {}).get("fingerprint"),
            tool_catalog_fingerprint=current_tool_fingerprint(),
            sources_backend=current_sources_fingerprint(),
            mapping_fingerprint=mapping_manifest()["fingerprint"],
        )
        # Backfill refresh: skip the read so the pipeline re-runs with current
        # logic; the fresh result is still written back to `pipeline_cache_key`.
        cached_response = (
            None if cache_refresh_active() else pipeline_cache.get(pipeline_cache_key)
        )
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
                    settings.format_duration(cached_seconds),
                )
                return ExtractionResult(response=response_model, run_id=run_id, cache_hit=True, classification=classification)

    try:
        orchestrator = orchestrator or PipelineOrchestrator(
            cache=cache, max_concurrent_agents=settings.resolve_max_concurrent_agents(),
        )
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
        raise ExtractionError(error_payload) from exc
    except Exception as exc:
        logger.exception("pipeline_execute failed: run_id=%s", run_id)
        error_payload = V2ErrorResponse(
            error_type=V2ErrorType.PIPELINE_ERROR,
            detail=str(exc),
            source_url=classification.normalized_url,
        )
        raise ExtractionError(error_payload) from exc

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
        warnings=pipeline_result.warnings,
        started_at=run_started_at,
        read_only=options.read_only,
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

    llm_dedup_executed = bool(entity_state.artifacts.llm_dedup_executed)
    llm_critic_executed = bool(entity_state.artifacts.llm_critic_executed)

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
            # Preserve field details in a domain error; the API adapter maps it
            # to an HTTP response while local callers receive the same details.
            root_error = exc.cause
            failure_message = (
                f"Root {root_error.entity_type} entity "
                f"'{root_error.entity_id}' failed strict validation"
            )
            logger.warning("%s: %s", settings.STAGE_OUTPUT_ASSEMBLY, failure_message)
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
            raise ExtractionError(error_payload) from exc
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
    link_veracity_seconds = pipeline_state.artifacts.link_veracity_seconds

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
    warnings = pipeline_state.warnings

    response_output: V2JSONLDOutput | V2JSONOutputEnvelope
    if output_format == "jsonld":
        response_output = V2JSONLDOutput.model_validate(shacl_graph_payload)
    else:
        response_output = V2JSONOutputEnvelope.model_validate(
            build_json_output(assembled_output),
        )
    context_summary_markdown: str | None = None
    if include_context_summary:
        summary_value = pipeline_state.gathered_context.get("compiled_context_markdown")
        if isinstance(summary_value, str):
            normalized_summary = summary_value.strip()
            if normalized_summary:
                context_summary_markdown = summary_value

    output_payload = response_output.model_dump(mode="json", by_alias=True)
    extract_graph = (
        settings.jsonld_to_graph(output_payload) if output_format == "jsonld" else None
    )
    final_entities = []
    if isinstance(assembled_output.root_entity, dict):
        final_entities.append(assembled_output.root_entity)
    final_entities.extend(assembled_output.related_entities)
    final_entity_count = len(final_entities)

    completed_stages = list(pipeline_result.stages_completed)
    stage_sequence = [settings.STAGE_PERMISSIVE_VALIDATION]
    if llm_dedup_executed:
        stage_sequence.append(settings.STAGE_LLM_DEDUP)
    stage_sequence.append(settings.STAGE_RECONCILIATION)
    if llm_critic_executed:
        stage_sequence.append(settings.STAGE_LLM_CRITIC)
    stage_sequence.extend(
        [
            settings.STAGE_STRICT_VALIDATION,
            settings.STAGE_OUTPUT_ASSEMBLY,
            settings.STAGE_LINK_VERACITY,
            settings.STAGE_JSONLD_BUILD,
            settings.STAGE_SHACL_GATE,
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
    other_seconds = max(
        0.0, total_seconds - orchestrator_seconds - link_veracity_seconds,
    )
    logger.info(
        "extract complete: run_id=%s url=%s entities=%d warnings=%d "
        "total=%s (orchestrator=%s, link_veracity=%s, other=%s)",
        run_id,
        classification.normalized_url,
        final_entity_count,
        len(warnings),
        settings.format_duration(total_seconds),
        settings.format_duration(orchestrator_seconds),
        settings.format_duration(link_veracity_seconds),
        settings.format_duration(other_seconds),
    )

    response_model = V2ExtractResponse(
        source_url=classification.normalized_url,
        detected_type=classification.detected_type.value,
        output_format=output_format,
        output=response_output,
        context_summary_markdown=context_summary_markdown,
        warnings=warnings,
        stats=stats,
        extraction_run=pipeline_state.artifacts.extraction_run,
        provenance=pipeline_state.artifacts.provenance,
        execution_profile=execution_profile,
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

    return ExtractionResult(response=response_model, run_id=run_id, cache_hit=False, classification=classification)
