from __future__ import annotations

import json
import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from typing import Annotated, Any, Literal
from urllib.parse import urlparse
from uuid import uuid4

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
    V2GraphResponse,
    V2HealthResponse,
)
from src.v2.pipeline import PipelineOrchestrator
from src.v2.pipeline.stages import assemble_intermediates, compute_stats

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

v2_router = APIRouter(prefix="/v2")


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


def _build_output_payload(
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


@v2_router.get(
    "/extract/{full_path:path}",
    response_model=V2ExtractResponse,
    response_model_exclude_none=True,
)
async def extract(  # noqa: PLR0913
    full_path: str,
    request: Request,
    *,
    output_format: Annotated[Literal["jsonld", "json"], Query()] = "jsonld",
    force_refresh: Annotated[bool, Query()] = False,
    include_intermediates: Annotated[bool, Query()] = False,
    providers: Annotated[ProviderSet, Depends(get_provider_set)],
) -> V2ExtractResponse | JSONResponse:
    try:
        classification = classify_github_url(full_path)
    except UnsupportedGitHubURL as exc:
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
            },
        )
    except Exception as exc:  # noqa: BLE001
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
        warnings.append("force_refresh requested for provider-backed pipeline run")

    output_payload = _build_output_payload(
        output_format=output_format,
        pipeline_agent_results=pipeline_result.agent_results,
    )

    config = V2Config()
    store = GraphStore(config.V2_GRAPH_DB_PATH)
    run_id = f"pipeline-{classification.detected_type}-{uuid4().hex}"

    response_intermediates = None
    if include_intermediates:
        response_intermediates = assemble_intermediates(
            source_url=classification.normalized_url,
            store=store,
            limit=DEFAULT_INTERMEDIATE_LIMIT,
        )

    entity_count = sum(
        1
        for result in pipeline_result.agent_results.values()
        if isinstance(result.data, dict) and result.data
    )
    extract_graph = _jsonld_to_graph(output_payload) if output_format == "jsonld" else None
    stats = compute_stats(store=store, run_id=run_id, graph=extract_graph)
    stats = stats.model_copy(
        update={
            "run_id": run_id,
            "entities_count": entity_count,
            "triples_count": stats.triples_count if extract_graph is not None else 0,
            "duration_ms": pipeline_result.duration_ms,
            "stages_completed": list(pipeline_result.stages_completed),
        },
    )

    return V2ExtractResponse(
        source_url=classification.normalized_url,
        detected_type=classification.detected_type,
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
