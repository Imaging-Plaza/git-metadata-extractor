from __future__ import annotations

import json
import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import JSONResponse
from rdflib import Graph as RDFGraph

from src.v2.agents import ProviderSet  # noqa: TC001
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
)
from src.v2.observability.context import RunContext
from src.v2.observability.error_events import record_error
from src.v2.observability.middleware import V2TracingMiddleware
from src.v2.observability.pipeline_spans import PipelineTracer
from src.v2.pipeline import PipelineOrchestrator
from src.v2.pipeline.stages import (
    RootEntityValidationError,
    assemble_intermediates,
    assemble_output,
    build_extract_output,
    compute_stats,
    reconcile_entities,
)
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
STAGE_SHACL_GATE = "shacl_gate"
STAGE_GRAPH_WRITE = "graph_write"
STAGE_OUTPUT_ASSEMBLY = "output_assembly"
LEGACY_STAGE_EXCLUSIONS = {
    "article_agents",
    "membership_agents",
    "contribution_agents",
}

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


def _build_legacy_output_payload(
    *,
    output_format: Literal["jsonld", "json"],
    pipeline_agent_results: dict[str, Any],
) -> dict[str, Any]:
    non_empty_entities = {
        key: value.data
        for key, value in pipeline_agent_results.items()
        if isinstance(value.data, dict) and value.data
    }

    if output_format == "jsonld":
        return {"@context": JSONLD_CONTEXT, "@graph": list(non_empty_entities.values())}
    return {"entities": non_empty_entities}


def _root_entity_type_for_detected_type(
    detected_type: Literal["repository", "user", "organization"],
) -> Literal["repository", "person", "organization"]:
    if detected_type == "user":
        return "person"
    return detected_type


@v2_router.get(
    "/extract/{full_path:path}",
    response_model=V2ExtractResponse,
    response_model_exclude_none=True,
)
async def extract(  # noqa: C901, PLR0912, PLR0913, PLR0915
    full_path: str,
    request: Request,
    *,
    output_format: Annotated[Literal["jsonld", "json"], Query()] = "jsonld",
    force_refresh: Annotated[bool, Query()] = False,
    include_intermediates: Annotated[bool, Query()] = False,
    providers: Annotated[ProviderSet, Depends(get_provider_set)],
) -> V2ExtractResponse | JSONResponse:
    store = GraphStore(V2Config().V2_GRAPH_DB_PATH)
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

    try:
        orchestrator = _get_orchestrator(request)
        execution_plan = orchestrator.get_execution_plan(classification.detected_type)
        pipeline_result = await orchestrator.execute(
            plan=execution_plan,
            providers=providers,
            context={
                "source_url": classification.normalized_url,
                "url_info": classification,
                "force_refresh": force_refresh,
                "run_id": run_id,
                "pipeline_tracer": tracer.child(),
            },
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
    if force_refresh:
        _append_unique_warning(
            warnings,
            "force_refresh requested for provider-backed pipeline run",
        )

    typed_entity_buckets = pipeline_result.resolved_typed_entity_buckets().to_dict()
    permissive_entity_count = sum(len(bucket) for bucket in typed_entity_buckets.values())
    with tracer.trace_stage(
        STAGE_PERMISSIVE_VALIDATION,
        detected_type=classification.detected_type.value,
    ) as stage_span:
        stage_span.set_attribute("entity_count", permissive_entity_count)

    with tracer.trace_stage(
        STAGE_RECONCILIATION,
        detected_type=classification.detected_type.value,
    ) as stage_span:
        reconciled = reconcile_entities(typed_entity_buckets)
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
    for warning in reconciled.synthesis_warnings:
        _append_unique_warning(warnings, warning)

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

    assembled_output = None
    legacy_output_entity_count = 0
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
            _append_unique_warning(warnings, str(exc))
            output_payload = _build_legacy_output_payload(
                output_format=output_format,
                pipeline_agent_results=pipeline_result.agent_results,
            )
            legacy_entities = output_payload.get("entities")
            if isinstance(legacy_entities, dict):
                legacy_output_entity_count = len(legacy_entities)
            elif output_format == "jsonld":
                graph_nodes = output_payload.get("@graph")
                if isinstance(graph_nodes, list):
                    legacy_output_entity_count = len(graph_nodes)
            stage_span.set_attribute("entity_count", legacy_output_entity_count)
        else:
            output_payload = build_extract_output(
                output_format=output_format,
                assembled=assembled_output,
                jsonld_context=JSONLD_CONTEXT,
            )
            stage_span.set_attributes(
                entity_count=1 + len(assembled_output.related_entities),
                excluded_count=len(assembled_output.excluded_entities),
            )
    if assembled_output is not None:
        for warning in assembled_output.warnings:
            _append_unique_warning(warnings, warning)

    shacl_graph_payload = None
    if assembled_output is not None:
        shacl_graph_payload = {
            "@context": JSONLD_CONTEXT,
            "@graph": [assembled_output.root_entity, *assembled_output.related_entities],
        }
    with tracer.trace_stage(
        STAGE_SHACL_GATE,
        detected_type=classification.detected_type.value,
    ) as stage_span:
        if shacl_graph_payload is None:
            stage_span.set_attribute("status", "skipped")
            _append_unique_warning(
                warnings,
                "SHACL validation skipped: no assembled root entity available",
            )
        else:
            shacl_data_graph = _jsonld_to_graph(shacl_graph_payload)
            if shacl_data_graph is None:
                stage_span.set_attribute("status", "skipped")
                _append_unique_warning(
                    warnings,
                    "SHACL validation skipped: unable to parse assembled graph payload",
                )
                shacl_data_graph = None
            if shacl_data_graph is None:
                pass
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
        output_entity_count = (
            1 + len(assembled_output.related_entities)
            if assembled_output is not None
            else legacy_output_entity_count
        )
        stage_span.set_attributes(
            status="skipped",
            entity_count=output_entity_count,
        )

    response_intermediates = None
    if include_intermediates:
        response_intermediates = assemble_intermediates(
            source_url=classification.normalized_url,
            store=store,
            limit=DEFAULT_INTERMEDIATE_LIMIT,
        )

    extract_graph = _jsonld_to_graph(output_payload) if output_format == "jsonld" else None
    if assembled_output is None:
        legacy_entities = output_payload.get("entities", {})
        legacy_values = legacy_entities.values() if isinstance(legacy_entities, dict) else []
        final_entity_ids = [
            entity_id
            for entity_id in (
                entity.get("id")
                for entity in legacy_values
                if isinstance(entity, dict)
            )
            if isinstance(entity_id, str) and entity_id
        ]
        final_entity_count = legacy_output_entity_count
    else:
        final_entity_ids = [
            entity_id
            for entity_id in (
                entity.get("id")
                for entity in [assembled_output.root_entity, *assembled_output.related_entities]
            )
            if isinstance(entity_id, str) and entity_id
        ]
        final_entity_count = len([assembled_output.root_entity, *assembled_output.related_entities])

    completed_stages = [
        stage_name
        for stage_name in pipeline_result.stages_completed
        if stage_name not in LEGACY_STAGE_EXCLUSIONS
    ]

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
            "excluded_entities_count": (
                len(assembled_output.excluded_entities)
                if assembled_output is not None
                else 0
            ),
        },
    )

    return V2ExtractResponse(
        source_url=classification.normalized_url,
        detected_type=classification.detected_type.value,
        output_format=output_format,
        output=output_payload,
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
