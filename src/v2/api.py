from __future__ import annotations

import json
import sys
from copy import deepcopy
from functools import lru_cache
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import JSONResponse
from rdflib import Graph as RDFGraph

from src.v2.agents import AgentRuntime, ProviderSet, parse_agent_runtime
from src.v2.config import V2Config
from src.v2.dependencies import get_provider_set
from src.v2.detection import UnsupportedGitHubURL, classify_github_url
from src.v2.graph.export import JSONLDExporter
from src.v2.graph.store import GraphStore
from src.v2.models import (
    V2ErrorResponse,
    V2ErrorType,
    V2ExtractResponse,
    V2FieldError,
    V2GraphResponse,
    V2HealthResponse,
    V2JSONLDOutput,
    V2JSONOutputEnvelope,
)
from src.v2.observability.context import RunContext
from src.v2.observability.error_events import record_error
from src.v2.observability.middleware import V2TracingMiddleware
from src.v2.observability.pipeline_spans import PipelineTracer
from src.v2.pipeline import PipelineOrchestrator
from src.v2.pipeline.stages import (
    AssembledOutput,
    RootEntityValidationError,
    apply_link_pruning_to_assembled_output,
    assemble_intermediates,
    assemble_output,
    build_json_output,
    build_jsonld_output,
    compute_stats,
    reconcile_entities,
    run_llm_critic_stage,
    run_llm_dedup_stage,
    run_link_veracity_stage,
)
from src.v2.pipeline.stages.context_gather import RequiredProviderUnavailableError
from src.v2.validation import (
    SHACLValidator,
    StrictSchemaValidator,
    load_ontology_shapes_graph,
)
from src.v2.validation.shacl_validation import SHACLRuntimeUnavailableError

DEFAULT_INTERMEDIATE_LIMIT = V2Config().V2_INTERMEDIATE_HISTORY_LIMIT
MIN_SUPPORTED_PYTHON = (3, 10)
PACKAGE_NAME = "git-metadata-extractor"
try:
    PACKAGE_VERSION = package_version(PACKAGE_NAME)
except PackageNotFoundError:
    PACKAGE_VERSION = "unknown"

MIN_SUBRESOURCE_PATH_SEGMENTS = 3
SUBRESOURCE_SEGMENT_INDEX = 2
JSONLD_CONTEXT = {
    "schema": "http://schema.org/",
    "pulse": "https://open-pulse.epfl.ch/ontology#",
    "org": "http://www.w3.org/ns/org#",
}

STAGE_CLASSIFY_URL = "classify_url"
STAGE_PERMISSIVE_VALIDATION = "permissive_validation"
STAGE_STRICT_VALIDATION = "strict_validation"
STAGE_RECONCILIATION = "reconciliation"
STAGE_LLM_DEDUP = "llm_dedup"
STAGE_LLM_CRITIC = "llm_critic"
STAGE_SHACL_GATE = "shacl_gate"
STAGE_GRAPH_WRITE = "graph_write"
STAGE_OUTPUT_ASSEMBLY = "output_assembly"
STAGE_JSONLD_BUILD = "jsonld_build"
STAGE_LINK_VERACITY = "link_veracity"
GRAPH_ENTITY_HELPER_KEYS = {"id", "type", "identifiers", "idSource", "shacl"}

v2_router = APIRouter(prefix="/v2", route_class=V2TracingMiddleware)


def _jsonld_to_graph(payload: dict[str, Any]) -> RDFGraph | None:
    try:
        graph = RDFGraph()
        graph.parse(data=json.dumps(payload), format="json-ld")
    except Exception:  # noqa: BLE001
        return None
    else:
        return graph


def _cap_intermediates_per_agent(
    intermediates: list[Any],
    per_agent_limit: int,
) -> list[Any]:
    if per_agent_limit <= 0:
        return []

    capped: list[Any] = []
    counts_by_agent: dict[str, int] = {}
    for envelope in intermediates:
        agent_name = getattr(envelope, "agent_name", None)
        if not isinstance(agent_name, str):
            continue

        current_count = counts_by_agent.get(agent_name, 0)
        if current_count >= per_agent_limit:
            continue

        counts_by_agent[agent_name] = current_count + 1
        capped.append(envelope)

    return capped


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

    orchestrator = PipelineOrchestrator()
    request.app.state.v2_orchestrator = orchestrator
    return orchestrator


def _get_pipeline_tracer(request: Request) -> PipelineTracer:
    run_id = getattr(request.state, "v2_run_id", None)
    if not isinstance(run_id, str) or not run_id:
        run_id = RunContext.get_run_id()
    if run_id:
        return PipelineTracer(run_id=str(run_id))
    return PipelineTracer()


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


@lru_cache(maxsize=1)
def _extract_jsonld_context() -> dict[str, Any]:
    payload = JSONLDExporter().get_context()
    raw_context = payload.get("@context")
    if isinstance(raw_context, dict):
        return raw_context
    return dict(JSONLD_CONTEXT)


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


def _to_graph_store_entity_payload(
    entity: dict[str, Any],
) -> tuple[str, str, dict[str, Any], dict[str, Any], str] | None:
    entity_id = entity.get("id")
    entity_type = entity.get("type")
    if not isinstance(entity_id, str) or not entity_id:
        return None
    if not isinstance(entity_type, str) or not entity_type:
        return None

    raw_identifiers = entity.get("identifiers")
    identifiers = raw_identifiers if isinstance(raw_identifiers, dict) else {}

    raw_id_source = entity.get("idSource")
    id_source = (
        raw_id_source
        if isinstance(raw_id_source, str) and raw_id_source
        else "schema:identifier"
    )

    data = {
        key: deepcopy(value)
        for key, value in entity.items()
        if key not in GRAPH_ENTITY_HELPER_KEYS and not key.startswith("_") and value is not None
    }

    return entity_type, entity_id, data, deepcopy(identifiers), id_source


@v2_router.get(
    "/extract/{full_path:path}",
    response_model=V2ExtractResponse,
    response_model_exclude_none=True,
)
async def extract(  # noqa: C901, PLR0911, PLR0912, PLR0913, PLR0915
    full_path: str,
    request: Request,
    *,
    output_format: Annotated[Literal["jsonld", "json"], Query()] = "jsonld",
    agent_runtime: Annotated[Literal["rule_based", "llm"] | None, Query()] = None,
    include_intermediates: Annotated[bool, Query()] = False,
    include_context_summary: Annotated[bool, Query()] = False,
    providers: Annotated[ProviderSet, Depends(get_provider_set)],
) -> V2ExtractResponse | JSONResponse:
    """Run the v2 extraction pipeline for a GitHub path."""

    config = V2Config()
    store = GraphStore(config.V2_GRAPH_DB_PATH)
    run_id: str | None = None

    try:
        classification = classify_github_url(full_path)
    except UnsupportedGitHubURL as exc:
        record_error(
            STAGE_CLASSIFY_URL,
            exc,
            run_id=RunContext.get_run_id() or None,
            source_url=exc.normalized_url,
        )
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
        record_error(
            STAGE_CLASSIFY_URL,
            exc,
            run_id=RunContext.get_run_id() or None,
            source_url=full_path,
        )
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

    run_id = store.create_run(
        classification.normalized_url,
        classification.detected_type.value,
    )
    request.state.v2_run_id = run_id
    RunContext.set_run_id(run_id)
    tracer = _get_pipeline_tracer(request)

    with tracer.trace_stage(STAGE_CLASSIFY_URL, source_url=full_path) as stage_span:
        stage_span.set_attribute("detected_type", classification.detected_type.value)
        stage_span.set_attribute("normalized_url", classification.normalized_url)
        # Query-level runtime overrides the configured default when present.
        resolved_runtime = parse_agent_runtime(
            agent_runtime,
            default=config.V2_AGENT_RUNTIME_DEFAULT,
            field_name="agent_runtime",
        )
        stage_span.set_attribute("agent_runtime", resolved_runtime.value)

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
                "pipeline_tracer": tracer.child(),
            },
        )
    except RequiredProviderUnavailableError as exc:
        store.fail_run(run_id, str(exc))
        record_error(
            "provider_preflight",
            exc,
            run_id=run_id,
            source_url=classification.normalized_url,
            detected_type=classification.detected_type.value,
        )
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
        store.fail_run(run_id, str(exc))
        record_error(
            "pipeline_execute",
            exc,
            run_id=run_id,
            source_url=classification.normalized_url,
            detected_type=classification.detected_type.value,
        )
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

    persisted_intermediates = 0
    for agent_name, agent_result in pipeline_result.agent_results.items():
        payload = agent_result.data
        if not isinstance(payload, dict) or not payload:
            continue
        try:
            store.insert_intermediate(
                source_url=classification.normalized_url,
                agent_name=agent_name,
                run_id=run_id,
                data=payload,
            )
        except Exception as exc:  # noqa: BLE001
            _append_unique_warning(
                warnings,
                f"Failed to persist intermediate for {agent_name}: {exc}",
            )
        else:
            persisted_intermediates += 1

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
    with tracer.trace_stage(
        STAGE_PERMISSIVE_VALIDATION,
        detected_type=classification.detected_type.value,
    ) as stage_span:
        stage_span.set_attribute("entity_count", permissive_entity_count)

    llm_dedup_executed = False
    if resolved_runtime == AgentRuntime.LLM:
        with tracer.trace_stage(
            STAGE_LLM_DEDUP,
            detected_type=classification.detected_type.value,
        ) as stage_span:
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
                stage_span.set_attribute("status", "error")
                _append_unique_warning(warnings, f"llm_dedup stage failed: {exc}")
            else:
                typed_entity_buckets = dedup_result.typed_entity_buckets
                stage_span.set_attributes(
                    accepted_cluster_count=dedup_result.accepted_cluster_count,
                    rejected_cluster_count=dedup_result.rejected_cluster_count,
                    remap_count=dedup_result.remap_count,
                )
                for warning in dedup_result.warnings:
                    _append_unique_warning(warnings, warning)

                if include_intermediates:
                    for agent_name, payload in (
                        ("llm_dedup_candidates", dedup_result.candidate_clusters),
                        ("llm_dedup_resolution", dedup_result.resolution),
                    ):
                        try:
                            store.insert_intermediate(
                                source_url=classification.normalized_url,
                                agent_name=agent_name,
                                run_id=run_id,
                                data=payload,
                            )
                        except Exception as exc:  # noqa: BLE001
                            _append_unique_warning(
                                warnings,
                                f"Failed to persist intermediate for {agent_name}: {exc}",
                            )
                        else:
                            persisted_intermediates += 1

    llm_critic_executed = False
    critic_pruned_excluded_entities: list[dict[str, Any]] = []
    with tracer.trace_stage(
        STAGE_RECONCILIATION,
        detected_type=classification.detected_type.value,
    ) as stage_span:
        reconciled = reconcile_entities(
            typed_entity_buckets,
        )
        stage_span.set_attributes(
            person_count=len(reconciled.entities.get("persons", [])),
            organization_count=len(reconciled.entities.get("organizations", [])),
            repository_count=len(reconciled.entities.get("repositories", [])),
            article_count=len(reconciled.entities.get("articles", [])),
            membership_count=len(reconciled.memberships),
            contribution_count=len(reconciled.contributions),
        )
    for warning in reconciled.link_warnings:
        _append_unique_warning(warnings, warning)
    if include_intermediates and isinstance(reconciled.reconciliation_debug, dict):
        try:
            store.insert_intermediate(
                source_url=classification.normalized_url,
                agent_name="reconciliation_debug",
                run_id=run_id,
                data=reconciled.reconciliation_debug,
            )
        except Exception as exc:  # noqa: BLE001
            _append_unique_warning(
                warnings,
                f"Failed to persist intermediate for reconciliation_debug: {exc}",
            )
        else:
            persisted_intermediates += 1

    if resolved_runtime == AgentRuntime.LLM:
        with tracer.trace_stage(
            STAGE_LLM_CRITIC,
            detected_type=classification.detected_type.value,
        ) as stage_span:
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
                )
            except Exception as exc:  # noqa: BLE001
                stage_span.set_attribute("status", "error")
                _append_unique_warning(warnings, f"llm_critic stage failed: {exc}")
            else:
                reconciled = critic_result.reconciled
                critic_pruned_excluded_entities = critic_result.pruned_excluded_entities
                stage_span.set_attributes(
                    proposed_drop_count=critic_result.applied.get("proposed_drop_count", 0),
                    applied_drop_count=critic_result.applied.get("applied_drop_count", 0),
                    protected_root_count=len(critic_result.applied.get("protected_root_ids", [])),
                )
                for warning in critic_result.warnings:
                    _append_unique_warning(warnings, warning)

                if include_intermediates:
                    for agent_name, payload in (
                        ("llm_critic_decisions", critic_result.decisions),
                        ("llm_critic_applied", critic_result.applied),
                    ):
                        try:
                            store.insert_intermediate(
                                source_url=classification.normalized_url,
                                agent_name=agent_name,
                                run_id=run_id,
                                data=payload,
                            )
                        except Exception as exc:  # noqa: BLE001
                            _append_unique_warning(
                                warnings,
                                f"Failed to persist intermediate for {agent_name}: {exc}",
                            )
                        else:
                            persisted_intermediates += 1

    strict_validation_entities = _iter_reconciled_entities(
        reconciled_entities=reconciled.entities,
        memberships=reconciled.memberships,
        contributions=reconciled.contributions,
    )
    with tracer.trace_stage(
        STAGE_STRICT_VALIDATION,
        detected_type=classification.detected_type.value,
    ) as stage_span:
        strict_batch = StrictSchemaValidator().validate_batch(strict_validation_entities)
        stage_span.set_attributes(
            valid_count=len(strict_batch.valid_entities),
            invalid_count=len(strict_batch.invalid_entities),
        )
    for warning in strict_batch.warnings:
        _append_unique_warning(warnings, f"Strict validation: {warning}")

    jsonld_context = _extract_jsonld_context()
    with tracer.trace_stage(
        STAGE_OUTPUT_ASSEMBLY,
        output_format=output_format,
    ) as stage_span:
        try:
            assembled_output = assemble_output(
                reconciled,
                strict_batch,
                root_entity_type=_root_entity_type_for_detected_type(
                    classification.detected_type.value,
                ),
            )
        except RootEntityValidationError as exc:
            stage_span.set_attribute("status", "error")
            failure_message = (
                f"Root {exc.entity_type} entity '{exc.entity_id}' failed strict validation"
            )
            store.fail_run(run_id, failure_message)
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
            stage_span.set_attribute("status", "warning")
            assembled_output = _build_rootless_assembled_output(
                reconciled=reconciled,
                strict_batch=strict_batch,
                root_warning=str(exc),
            )
            stage_span.set_attributes(
                root_present=False,
                entity_count=len(assembled_output.related_entities),
                excluded_count=len(assembled_output.excluded_entities),
            )
        else:
            root_present = isinstance(assembled_output.root_entity, dict)
            stage_span.set_attributes(
                root_present=root_present,
                entity_count=len(assembled_output.related_entities) + (1 if root_present else 0),
                excluded_count=len(assembled_output.excluded_entities),
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

    with tracer.trace_stage(
        STAGE_LINK_VERACITY,
        detected_type=classification.detected_type.value,
    ) as stage_span:
        entities_for_link_validation: list[dict[str, Any]] = []
        if isinstance(assembled_output.root_entity, dict):
            entities_for_link_validation.append(deepcopy(assembled_output.root_entity))
        entities_for_link_validation.extend(
            deepcopy(entity)
            for entity in assembled_output.related_entities
            if isinstance(entity, dict)
        )

        try:
            # TODO(graph-cache): Cache link-veracity verdicts by normalized link + model + relation context.
            # TODO(graph-cache): Define cache invalidation strategy tied to source entity and graph changes.
            # TODO(graph-cache): Integrate link-veracity lookups with future graph-cache read-through/write-through policy.
            link_veracity_result = await run_link_veracity_stage(
                entities=entities_for_link_validation,
                source_url=classification.normalized_url,
                providers=providers,
                max_concurrency=orchestrator.max_concurrent_agents,
            )
        except Exception as exc:  # noqa: BLE001
            stage_span.set_attribute("status", "error")
            _append_unique_warning(warnings, f"Link veracity stage failed: {exc}")
        else:
            stage_span.set_attributes(
                checked_count=link_veracity_result.checked_count,
                supported_count=link_veracity_result.supported_count,
                unsupported_count=link_veracity_result.unsupported_count,
                failed_count=link_veracity_result.failed_count,
                invalid_link_count=len(link_veracity_result.invalid_links),
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
            for record in link_veracity_result.records:
                try:
                    store.insert_intermediate(
                        source_url=classification.normalized_url,
                        agent_name=STAGE_LINK_VERACITY,
                        run_id=run_id,
                        data=record,
                    )
                except Exception as exc:  # noqa: BLE001
                    _append_unique_warning(
                        warnings,
                        f"Failed to persist intermediate for {STAGE_LINK_VERACITY}: {exc}",
                    )
                else:
                    persisted_intermediates += 1

            invalid_links = set(link_veracity_result.invalid_links)
            if invalid_links:
                assembled_output, link_pruning_warnings = apply_link_pruning_to_assembled_output(
                    assembled=assembled_output,
                    invalid_links=invalid_links,
                    entity_link_map=link_veracity_result.entity_link_map,
                    article_identifier_link_map=link_veracity_result.article_identifier_link_map,
                )
                for warning in link_pruning_warnings:
                    _append_unique_warning(warnings, warning)

    with tracer.trace_stage(
        STAGE_JSONLD_BUILD,
        output_format=output_format,
    ) as stage_span:
        shacl_graph_payload = build_jsonld_output(
            assembled=assembled_output,
            jsonld_context=jsonld_context,
        )
        graph_nodes = shacl_graph_payload.get("@graph")
        stage_span.set_attributes(
            entity_count=len(graph_nodes) if isinstance(graph_nodes, list) else 0,
            context_terms=len(jsonld_context),
        )

    with tracer.trace_stage(
        STAGE_SHACL_GATE,
        detected_type=classification.detected_type.value,
    ) as stage_span:
        shacl_data_graph = _jsonld_to_graph(shacl_graph_payload)
        if shacl_data_graph is None:
            stage_span.set_attribute("status", "skipped")
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
                stage_span.set_attribute("status", "skipped")
                _append_unique_warning(warnings, str(exc))
            except Exception as exc:  # noqa: BLE001
                stage_span.set_attribute("status", "error")
                _append_unique_warning(
                    warnings,
                    f"SHACL validation failed: {exc}",
                )
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
                stage_span.set_attributes(
                    conforms=shacl_result.conforms,
                    violation_count=len(shacl_result.violations),
                    warning_count=len(shacl_result.warnings),
                )

    with tracer.trace_stage(
        STAGE_GRAPH_WRITE,
        detected_type=classification.detected_type.value,
    ) as stage_span:
        final_entities: list[dict[str, Any]] = []
        if isinstance(assembled_output.root_entity, dict):
            final_entities.append(assembled_output.root_entity)
        final_entities.extend(assembled_output.related_entities)

        graph_entities_upserted = 0
        skipped_entities = 0
        try:
            for entity in final_entities:
                parsed = _to_graph_store_entity_payload(entity)
                if parsed is None:
                    skipped_entities += 1
                    _append_unique_warning(
                        warnings,
                        "Skipped graph write for an entity without valid id/type",
                    )
                    continue

                entity_type, entity_id, data, identifiers, id_source = parsed
                store.upsert_entity(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    data=data,
                    identifiers=identifiers,
                    id_source=id_source,
                    source="extract",
                    run_id=run_id,
                )
                graph_entities_upserted += 1
        except Exception as exc:  # noqa: BLE001
            stage_span.set_attribute("status", "error")
            failure_message = f"Graph write failed: {exc}"
            store.fail_run(run_id, failure_message)
            record_error(
                STAGE_GRAPH_WRITE,
                exc,
                run_id=run_id,
                source_url=classification.normalized_url,
                detected_type=classification.detected_type.value,
            )
            error_payload = V2ErrorResponse(
                error_type=V2ErrorType.PIPELINE_ERROR,
                detail=failure_message,
                source_url=classification.normalized_url,
            )
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content=error_payload.model_dump(mode="json", exclude_none=True),
            )

        stage_span.set_attributes(
            status="success" if skipped_entities == 0 else "warning",
            entity_count=graph_entities_upserted,
            skipped_count=skipped_entities,
            intermediates_written=persisted_intermediates,
            mode="upsert",
        )

    response_intermediates = None
    if include_intermediates:
        response_intermediates = assemble_intermediates(
            source_url=classification.normalized_url,
            store=store,
            limit=DEFAULT_INTERMEDIATE_LIMIT,
            run_id=run_id,
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
    final_entity_ids = [
        entity_id
        for entity_id in (
            entity.get("id")
            for entity in final_entities
        )
        if isinstance(entity_id, str) and entity_id
    ]
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
            STAGE_GRAPH_WRITE,
        ],
    )
    for stage_name in stage_sequence:
        if stage_name not in completed_stages:
            completed_stages.append(stage_name)
    stats = compute_stats(store=store, run_id=run_id, graph=extract_graph)
    stats = stats.model_copy(
        update={
            "run_id": run_id,
            "entities_count": final_entity_count,
            "triples_count": stats.triples_count if extract_graph is not None else 0,
            "duration_ms": pipeline_result.duration_ms,
            "stages_completed": completed_stages,
        },
    )
    store.complete_run(
        run_id,
        {
            "duration_ms": pipeline_result.duration_ms,
            "stages_completed": completed_stages,
            "entity_ids": final_entity_ids,
            "entities_count": final_entity_count,
            "excluded_entities_count": len(assembled_output.excluded_entities),
        },
    )

    return V2ExtractResponse(
        source_url=classification.normalized_url,
        detected_type=classification.detected_type.value,
        output_format=output_format,
        output=response_output,
        context_summary_markdown=context_summary_markdown,
        warnings=warnings,
        stats=stats,
        intermediates=response_intermediates,
    )


@v2_router.get(
    "/graph",
    response_model=V2GraphResponse,
    response_model_exclude_none=True,
)
async def graph(
    *,
    source_url: Annotated[str | None, Query()] = None,
    entity_type: Annotated[list[str] | None, Query()] = None,
    include_intermediates: Annotated[bool, Query()] = True,
    intermediate_limit: Annotated[int, Query(ge=0)] = DEFAULT_INTERMEDIATE_LIMIT,
) -> V2GraphResponse:
    config = V2Config()
    store = GraphStore(config.V2_GRAPH_DB_PATH)
    exporter = JSONLDExporter()
    graph_jsonld = exporter.export_filtered(
        store.get_rdf_graph(),
        source_url=source_url,
        entity_types=entity_type,
        store=store,
    )

    response_intermediates = None
    if include_intermediates:
        all_intermediates = assemble_intermediates(
            source_url=source_url,
            store=store,
            limit=None,
        )
        response_intermediates = _cap_intermediates_per_agent(
            all_intermediates,
            intermediate_limit,
        )

    filtered_graph = _jsonld_to_graph(graph_jsonld)
    stats = compute_stats(
        store=store,
        run_id="graph-export",
        graph=filtered_graph,
    )
    return V2GraphResponse(
        graph_jsonld=graph_jsonld,
        intermediates=response_intermediates,
        stats=stats.model_copy(
            update={
                "stages_completed": ["graph_export"],
            },
        ),
    )


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
        "graph_store": "healthy",
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
