from __future__ import annotations

import asyncio
import json
import logging
import os
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from typing import Any
from urllib.parse import urlparse

from fastapi import Request
from rdflib import Graph as RDFGraph

from git_metadata_extractor.api_models import (
    V2ExtractJobStatus,
)
from git_metadata_extractor.dependencies import _resolve_provider_cache
from git_metadata_extractor.providers.cache import (
    ProviderCache,
)
from git_metadata_extractor.jobs import JobStore
from git_metadata_extractor.pipeline import PipelineOrchestrator

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



def _auto_ingest_enabled(canonical: str, *aliases: str) -> bool:
    """Return True if the canonical auto-ingest env flag — or a deprecated
    alias — is set to "true".

    The canonical name takes precedence; when a request is enabled via an alias
    we log a deprecation warning so operators can migrate. This exists because
    the only historically-documented flag (`V2_GITHUB_RAG_AUTO_INGEST`) never
    matched the name the code reads (`V2_GITHUB_REPOS_RAG_AUTO_INGEST`), so
    operators who followed the docs silently got no auto-ingest.
    """
    for name in (canonical, *aliases):
        raw = os.getenv(name)
        if raw is not None and raw.strip().lower() == "true":
            if name != canonical:
                logger.warning(
                    "auto-ingest enabled via deprecated env var %s; rename it to "
                    "%s (the alias may be removed in a future release)",
                    name,
                    canonical,
                )
            return True
    return False


STAGE_CLASSIFY_URL = "classify_url"
STAGE_PERMISSIVE_VALIDATION = "permissive_validation"
STAGE_STRICT_VALIDATION = "strict_validation"
STAGE_RECONCILIATION = "reconciliation"
STAGE_LLM_DEDUP = "llm_dedup"
STAGE_LLM_CRITIC = "llm_critic"
STAGE_SHACL_GATE = "shacl_gate"

# Async-job heartbeat tuning. The worker writes `last_heartbeat_at` to
# the JobStore every `_JOB_HEARTBEAT_INTERVAL_SECONDS`. When a GET
# arrives for a "running" job whose latest heartbeat is older than
# `_JOB_STALE_THRESHOLD_SECONDS`, we treat the job as orphaned (worker
# died, OS killed it, deploy restarted, etc.) and flip it to FAILED so
# the client doesn't poll forever. Threshold is generous (10 minutes
# = 20 missed beats) so we don't false-positive on a slow LLM stage.
_JOB_HEARTBEAT_INTERVAL_SECONDS = 30.0
_JOB_STALE_THRESHOLD_SECONDS = 600.0
STAGE_OUTPUT_ASSEMBLY = "output_assembly"
STAGE_JSONLD_BUILD = "jsonld_build"
STAGE_LINK_VERACITY = "link_veracity"
STAGE_REFINE_WITH_LLM = "refine_with_llm"
STAGE_RESOLVE_COMPANY_TO_ROR = "resolve_company_to_ror"
STAGE_RESOLVE_BIO_TO_ROR = "resolve_bio_to_ror"
STAGE_RESOLVE_BIO_TO_ROR_LLM = "resolve_bio_to_ror_llm"
STAGE_RESOLVE_PLACEHOLDER_ORGS_TO_ROR = "resolve_placeholder_orgs_to_ror"



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


def _is_link_veracity_enabled() -> bool:
    """Read `V2_LINK_VERACITY_ENABLED` env var (default true).

    When false, the link-veracity LLM stage is skipped entirely. Useful for
    broad batch runs where you want fast extraction and don't need every
    URL re-verified against fetched page content. Saves ~1–3 minutes per
    repo and a chunk of LLM calls.
    """
    raw = os.getenv("V2_LINK_VERACITY_ENABLED")
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


def _resolve_company_to_ror_enabled() -> bool:
    """Read `V2_RESOLVE_COMPANY_TO_ROR` env var (default true).

    When true (the default), the company → ROR resolver runs right after
    `reconcile_entities` and stamps `schema:affiliation` on persons whose
    `gme-internal:company` string resolves confidently against ROR.
    Set to `false` (or `0`/`no`/`off`) to skip the stage; useful for
    catalog backfills that should preserve the raw company strings
    unchanged.
    """
    raw = os.getenv("V2_RESOLVE_COMPANY_TO_ROR")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "f", "no", "n", "off"}


def _resolve_bio_to_ror_enabled() -> bool:
    """Read `V2_RESOLVE_BIO_TO_ROR` env var (default true).

    When true (the default), the bio → ROR resolver runs right after
    `resolve_company_to_ror` and stamps `schema:affiliation` on persons
    whose `_company` was empty but whose `_bio` / `_orcid_biography` /
    `_blog` carry an institution signal that the strict ROR gate accepts.
    Set to `false` (or `0`/`no`/`off`) to skip; useful when a catalog
    backfill wants the structured-field-only behaviour.
    """
    raw = os.getenv("V2_RESOLVE_BIO_TO_ROR")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "f", "no", "n", "off"}


def _resolve_bio_to_ror_llm_enabled() -> bool:
    """Read `V2_RESOLVE_BIO_TO_ROR_LLM` env var (default true).

    When true (the default) AND the request runs under the LLM / hybrid
    runtime, the LLM bio resolver runs after `resolve_bio_to_ror` and
    spends one LLM call per still-unaffiliated person to resolve
    affiliations that only surface in prose (long profile READMEs,
    bios without a clean `at X` shape). Gated by runtime as well as
    this flag — under the rule-based runtime it never runs even when
    true.
    """
    raw = os.getenv("V2_RESOLVE_BIO_TO_ROR_LLM")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "f", "no", "n", "off"}


def _resolve_placeholder_orgs_to_ror_enabled() -> bool:
    """Read `V2_RESOLVE_PLACEHOLDER_ORGS_TO_ROR` env var (default true).

    When true (the default), the placeholder-resolver stage runs after
    the three Person-side resolver stages and rewrites Organization
    entities whose `idSource = "uuid"` (and which carry a `schema:name`
    breadcrumb) into ROR-anchored Orgs, patching every referring
    Membership composite in the same pass. Set to `false` to leave the
    `urn:pulse:<uuid>` Org bucket untouched.
    """
    raw = os.getenv("V2_RESOLVE_PLACEHOLDER_ORGS_TO_ROR")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "f", "no", "n", "off"}


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


def _resolve_max_concurrent_agents() -> int:
    """Read `V2_MAX_CONCURRENT_AGENTS` env var (default 8).

    Caps how many work items per stage (person agents, contribution agents,
    link-veracity calls, etc.) run in parallel within a single /extract
    request. Higher values speed up wide-fanout repos at the cost of more
    concurrent LLM calls — keep within the LLM provider's rate limit.

    Default raised from 6 to 8 after profiling a 50-person repo
    (deeplabcut/deeplabcut): person+membership stages were spending ~12
    minutes waiting on the semaphore. The RCP/LLM stack absorbed 8
    in-flight calls without thermal throttling in that test.
    """
    raw = os.getenv("V2_MAX_CONCURRENT_AGENTS")
    if raw is None:
        return 8
    try:
        value = int(raw.strip())
    except ValueError:
        return 8
    return max(1, value)


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
        request.app.state, "_v2_job_task_by_id", None,
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

