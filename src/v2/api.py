from __future__ import annotations

import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from typing import Annotated, Literal
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import APIRouter, Query, status
from fastapi.responses import JSONResponse

from src.v2.config import V2Config
from src.v2.detection import UnsupportedGitHubURL, classify_github_url
from src.v2.models import (
    V2ErrorResponse,
    V2ErrorType,
    V2ExtractResponse,
    V2GraphResponse,
    V2HealthResponse,
    V2Stats,
)

DEFAULT_INTERMEDIATE_LIMIT = V2Config().V2_INTERMEDIATE_HISTORY_LIMIT
MIN_SUPPORTED_PYTHON = (3, 10)
PACKAGE_NAME = "git-metadata-extractor"
try:
    PACKAGE_VERSION = package_version(PACKAGE_NAME)
except PackageNotFoundError:
    PACKAGE_VERSION = "unknown"

MIN_SUBRESOURCE_PATH_SEGMENTS = 3
SUBRESOURCE_SEGMENT_INDEX = 2

v2_router = APIRouter(prefix="/v2")


def _build_stats(*, stage_name: str) -> V2Stats:
    return V2Stats(
        entities_count=0,
        triples_count=0,
        run_id=f"stub-{stage_name}-{uuid4().hex}",
        duration_ms=0,
        stages_completed=[stage_name],
    )


def _extract_path_kind(source_url: str) -> str | None:
    candidate_url = source_url.strip()
    if "://" not in candidate_url:
        candidate_url = f"https://{candidate_url}"

    parsed_url = urlparse(candidate_url)
    path_segments = [segment for segment in parsed_url.path.split("/") if segment]
    if len(path_segments) >= MIN_SUBRESOURCE_PATH_SEGMENTS:
        return path_segments[SUBRESOURCE_SEGMENT_INDEX].lower()
    return None


@v2_router.get(
    "/extract/{full_path:path}",
    response_model=V2ExtractResponse,
    response_model_exclude_none=True,
)
async def extract(
    full_path: str,
    *,
    output_format: Annotated[Literal["jsonld", "json"], Query()] = "jsonld",
    force_refresh: Annotated[bool, Query()] = False,
    include_intermediates: Annotated[bool, Query()] = False,
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

    response_warnings: list[str] = []
    if force_refresh:
        response_warnings.append("force_refresh is ignored in the stub implementation")

    output_payload: dict[str, object]
    if output_format == "jsonld":
        output_payload = {"@context": {}, "@graph": []}
    else:
        output_payload = {"status": "stub"}

    response_intermediates = None
    if include_intermediates:
        response_intermediates = [
            {
                "stage": "extract_stub",
                "owner": classification.owner,
                "repo": classification.repo,
            },
        ]

    return V2ExtractResponse(
        source_url=classification.normalized_url,
        detected_type=classification.detected_type,
        output_format=output_format,
        output=output_payload,
        warnings=response_warnings,
        stats=_build_stats(stage_name="extract"),
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
    response_intermediates = None
    if include_intermediates:
        response_intermediates = [
            {
                "stage": "graph_stub",
                "source_url": source_url,
                "entity_type": entity_type or [],
                "intermediate_limit": intermediate_limit,
            },
        ]

    return V2GraphResponse(
        graph_jsonld={"@context": {}, "@graph": []},
        intermediates=response_intermediates,
        stats=_build_stats(stage_name="graph"),
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
