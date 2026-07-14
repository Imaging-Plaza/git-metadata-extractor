from __future__ import annotations

import logging
import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from typing import Annotated, Any, Literal

from fastapi import Depends, Request, status
from fastapi.responses import JSONResponse

from git_metadata_extractor.api_models import (
    V2HealthResponse,
)
from git_metadata_extractor.auth import verify_token
from git_metadata_extractor.config import V2Config
from git_metadata_extractor.providers.cache import (
    ProviderCache,
)
from git_metadata_extractor.observation.github_rate_limit import (
    GitHubRateLimitSummary,
    probe_github_rate_limit,
)

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


from ._router import v2_router

@v2_router.post(
    "/cache/clear",
    tags=["Cache Management"],
)
async def clear_v2_cache(
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> dict[str, Any]:
    """Wipe every entry from the v2 pipeline cache.

    Targets the `ProviderCache` SQLite at `V2_PROVIDER_CACHE_PATH` — the
    same store that backs the `/extract` short-circuit and the per-provider
    sub-caches (RAG, Selenium, link veracity, etc.).
    """

    cache = getattr(request.app.state, "v2_provider_cache", None)
    if not isinstance(cache, ProviderCache):
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "v2 provider cache is not configured"},
        )
    removed = cache.clear()
    logger.info("v2 cache cleared: removed=%d entries", removed)
    return {"message": f"Cleared {removed} v2 cache entries", "removed": removed}



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

    rate_limit_summary: GitHubRateLimitSummary | None = None
    if config and config.GME_GITHUB_TOKEN:
        try:
            rate_limit_summary = probe_github_rate_limit()
        except Exception:
            logger.exception("github rate-limit probe failed")
            rate_limit_summary = None
        component_statuses["github_token"] = (
            rate_limit_summary.status if rate_limit_summary is not None else "degraded"
        )
    else:
        component_statuses["github_token"] = "degraded"

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
        github_rate_limit=rate_limit_summary,
    )
