from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

from git_metadata_extractor.api_models import (
    V2ExtractJobStatus,
)
from git_metadata_extractor.dependencies import _resolve_provider_cache
from git_metadata_extractor.jobs import JobStore
from git_metadata_extractor.pipeline import PipelineOrchestrator
from git_metadata_extractor.pipeline import settings as pipeline_settings
from git_metadata_extractor.providers.cache import (
    ProviderCache,
)

if TYPE_CHECKING:
    import asyncio

    from fastapi import Request



# Compatibility names remain available; settings has the single implementation.
_is_pipeline_cache_enabled = pipeline_settings.is_pipeline_cache_enabled
_is_link_veracity_enabled = pipeline_settings.is_link_veracity_enabled
_should_apply_critic_pruning = pipeline_settings.should_apply_critic_pruning
_canonical_output_enabled = pipeline_settings.canonical_output_enabled
_resolve_company_to_ror_enabled = pipeline_settings.resolve_company_to_ror_enabled
_resolve_bio_to_ror_enabled = pipeline_settings.resolve_bio_to_ror_enabled
_resolve_bio_to_ror_llm_enabled = pipeline_settings.resolve_bio_to_ror_llm_enabled
_resolve_placeholder_orgs_to_ror_enabled = pipeline_settings.resolve_placeholder_orgs_to_ror_enabled
_format_duration = pipeline_settings.format_duration
_jsonld_to_graph = pipeline_settings.jsonld_to_graph
_extract_path_kind = pipeline_settings.extract_path_kind
_resolve_max_concurrent_agents = pipeline_settings.resolve_max_concurrent_agents
MIN_SUPPORTED_PYTHON = pipeline_settings.MIN_SUPPORTED_PYTHON
PACKAGE_NAME = pipeline_settings.PACKAGE_NAME
MIN_SUBRESOURCE_PATH_SEGMENTS = pipeline_settings.MIN_SUBRESOURCE_PATH_SEGMENTS
SUBRESOURCE_SEGMENT_INDEX = pipeline_settings.SUBRESOURCE_SEGMENT_INDEX
JSONLD_CONTEXT_FALLBACK = pipeline_settings.JSONLD_CONTEXT_FALLBACK
STAGE_PERMISSIVE_VALIDATION = pipeline_settings.STAGE_PERMISSIVE_VALIDATION
STAGE_STRICT_VALIDATION = pipeline_settings.STAGE_STRICT_VALIDATION
STAGE_RECONCILIATION = pipeline_settings.STAGE_RECONCILIATION
STAGE_LLM_DEDUP = pipeline_settings.STAGE_LLM_DEDUP
STAGE_LLM_CRITIC = pipeline_settings.STAGE_LLM_CRITIC
STAGE_SHACL_GATE = pipeline_settings.STAGE_SHACL_GATE
STAGE_OUTPUT_ASSEMBLY = pipeline_settings.STAGE_OUTPUT_ASSEMBLY
STAGE_JSONLD_BUILD = pipeline_settings.STAGE_JSONLD_BUILD
STAGE_LINK_VERACITY = pipeline_settings.STAGE_LINK_VERACITY
_TRUTHY_ENV_VALUES = pipeline_settings._TRUTHY_ENV_VALUES  # noqa: SLF001 - compatibility constant
PACKAGE_VERSION = pipeline_settings.PACKAGE_VERSION

logger = logging.getLogger(__name__)


def _auto_ingest_enabled(canonical: str, *aliases: str) -> bool:
    """Return True if the canonical auto-ingest env flag — or a deprecated
    alias — is set to "true".

    The canonical name takes precedence: when it is set, its value alone
    decides, so an explicit "false" is not overridden by a leftover alias.
    Aliases are consulted only when the canonical name is unset; when a request
    is enabled via an alias we log a deprecation warning so operators can
    migrate. This exists because the only historically-documented flag
    (`V2_GITHUB_RAG_AUTO_INGEST`) never matched the name the code reads
    (`V2_GITHUB_REPOS_RAG_AUTO_INGEST`), so operators who followed the docs
    silently got no auto-ingest.
    """
    canonical_raw = os.getenv(canonical)
    if canonical_raw is not None:
        return canonical_raw.strip().lower() == "true"
    for name in aliases:
        raw = os.getenv(name)
        if raw is not None and raw.strip().lower() == "true":
            logger.warning(
                "auto-ingest enabled via deprecated env var %s; rename it to "
                "%s (the alias may be removed in a future release)",
                name,
                canonical,
            )
            return True
    return False


# Async-job heartbeat tuning. The worker writes `last_heartbeat_at` to
# the JobStore every `_JOB_HEARTBEAT_INTERVAL_SECONDS`. When a GET
# arrives for a "running" job whose latest heartbeat is older than
# `_JOB_STALE_THRESHOLD_SECONDS`, we treat the job as orphaned (worker
# died, OS killed it, deploy restarted, etc.) and flip it to FAILED so
# the client doesn't poll forever. Threshold is generous (10 minutes
# = 20 missed beats) so we don't false-positive on a slow LLM stage.
_JOB_HEARTBEAT_INTERVAL_SECONDS = 30.0
_JOB_STALE_THRESHOLD_SECONDS = 600.0


def _get_orchestrator(request: Request) -> PipelineOrchestrator:
    existing = getattr(request.app.state, "v2_orchestrator", None)
    if isinstance(existing, PipelineOrchestrator):
        return existing

    cache = getattr(request.app.state, "v2_provider_cache", None)
    if not isinstance(cache, ProviderCache):
        cache = None
    orchestrator = PipelineOrchestrator(
        cache=cache,
        max_concurrent_agents=_resolve_max_concurrent_agents(),
    )
    request.app.state.v2_orchestrator = orchestrator
    return orchestrator


def _resolve_job_store(request: Request) -> JobStore | None:
    """Resolve a JobStore backed by the shared ProviderCache, if available."""
    cache = _resolve_provider_cache(request.app.state)
    if not isinstance(cache, ProviderCache):
        return None
    return JobStore(cache)


_TERMINAL_JOB_STATUSES = frozenset(
    {
        V2ExtractJobStatus.COMPLETED,
        V2ExtractJobStatus.FAILED,
        V2ExtractJobStatus.CANCELLED,
    },
)


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


def _register_job_task(request: Request, job_id: str, task: asyncio.Task[Any]) -> None:
    """Index a running extract job's task by job id so it can be cancelled."""
    registry: dict[str, asyncio.Task[Any]] | None = getattr(
        request.app.state,
        "_v2_job_task_by_id",
        None,
    )
    if registry is None:
        registry = {}
        request.app.state._v2_job_task_by_id = registry  # noqa: SLF001
    registry[job_id] = task
    task.add_done_callback(lambda _t: registry.pop(job_id, None))


def _get_job_task(request: Request, job_id: str) -> asyncio.Task[Any] | None:
    registry = getattr(request.app.state, "_v2_job_task_by_id", None)
    if not isinstance(registry, dict):
        return None
    return registry.get(job_id)


def _job_status_path(job_id: str) -> str:
    return f"/v2/jobs/{job_id}"


# Compatibility exports; pipeline settings are owned below the HTTP layer.
