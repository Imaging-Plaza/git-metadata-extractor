"""FastAPI dependency adapter over transport-independent provider composition."""
# ruff: noqa: PLC0414 - compatibility exports for development scripts

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import Depends, Request

from git_metadata_extractor.agents import ProviderSet
from git_metadata_extractor.providers.detection import (
    UnsupportedGitHubURL,
    classify_github_url,
)

# Preserve development-script and API-helper imports.
from git_metadata_extractor.providers.runtime import (
    _default_provider_set as _default_provider_set,
)
from git_metadata_extractor.providers.runtime import (
    _resolve_provider_cache as _resolve_provider_cache,
)
from git_metadata_extractor.providers.runtime import build_provider_resources


def _is_repository_extract_request(request: Request) -> bool:
    full_path = request.path_params.get("full_path")
    if not isinstance(full_path, str) or not full_path.strip():
        return False

    try:
        classification = getattr(
            request.state, "v2_classification", None,
        ) or classify_github_url(full_path)
    except (UnsupportedGitHubURL, ValueError):
        return False

    detected_type = classification.detected_type
    return str(getattr(detected_type, "value", detected_type)) == "repository"


async def get_provider_set(request: Request) -> ProviderSet:
    resources = build_provider_resources(
        request.app.state,
        repository_scope=False
        if isinstance(getattr(request.app.state, "v2_provider_set", None), ProviderSet)
        else _is_repository_extract_request(request),
    )
    request.state.v2_provider_resources = resources
    return resources.bundle


async def provider_dependency(
    request: Request, bundle: Annotated[ProviderSet, Depends(get_provider_set)],
):
    try:
        yield bundle
    finally:
        resources = getattr(request.state, "v2_provider_resources", None)
        if resources is not None and not getattr(
            request.state, "v2_resources_deferred", False,
        ):
            await resources.aclose()


def defer_provider_resources(request: Request, task: asyncio.Task) -> None:
    """Transfer request-owned clients to a job, including pre-start cancellation."""
    request.state.v2_resources_deferred = True
    resources = getattr(request.state, "v2_provider_resources", None)
    if resources is None:
        return
    tasks = getattr(request.app.state, "_v2_provider_cleanup_tasks", None)
    if tasks is None:
        tasks = set()
        request.app.state._v2_provider_cleanup_tasks = tasks  # noqa: SLF001

    def release(_task):
        cleanup = asyncio.create_task(resources.aclose())
        tasks.add(cleanup)
        cleanup.add_done_callback(tasks.discard)

    task.add_done_callback(release)
