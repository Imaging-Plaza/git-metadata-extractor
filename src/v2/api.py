from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sys
import threading
from copy import deepcopy
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from time import perf_counter
from typing import Annotated, Any, Literal
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import APIRouter, Depends, Path, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from rdflib import Graph as RDFGraph

from src.v2.agents import AgentRuntime, ProviderSet, parse_agent_runtime
from src.v2.agents.llm.refiners.ror_parent.agent import RorParentSelectorAgent
from src.v2.api_models import (
    DockerhubIngestRequest,
    EthzResearchCollectionIngestRequest,
    GitHubIngestRequest,
    GitHubOrgsIngestRequest,
    GitHubUsersIngestRequest,
    HuggingFaceDatasetsIngestRequest,
    HuggingFaceModelsIngestRequest,
    HuggingFaceOrganizationsIngestRequest,
    HuggingFacePapersIngestRequest,
    HuggingFaceSpacesIngestRequest,
    HuggingFaceUsersIngestRequest,
    IndexIngestJob,
    IndexIngestJobAccepted,
    IndexIngestJobStatus,
    IndexSearchRequest,
    IndexSearchResponse,
    OamonitorIngestRequest,
    OpenAlexIngestRequest,
    OrcidIngestRequest,
    RenkulabIngestRequest,
    SwissubaseIngestRequest,
    V2ErrorResponse,
    V2ErrorType,
    V2ExtractJob,
    V2ExtractJobAccepted,
    V2ExtractJobStatus,
    V2ExtractRequest,
    V2ExtractResponse,
    V2FieldError,
    V2HealthResponse,
    V2JSONLDOutput,
    V2JSONOutputEnvelope,
    ZenodoIngestRequest,
)
from src.v2.auth import verify_token
from src.v2.config import V2Config
from src.v2.dependencies import _resolve_provider_cache, get_provider_set
from src.v2.indices.dockerhub import (
    run_dockerhub_ingest_job,
    run_dockerhub_search,
)
from src.v2.indices.ethz_research_collection import (
    run_ethz_research_collection_ingest_job,
    run_ethz_research_collection_search,
)
from src.v2.indices.github_repos import (
    run_github_repos_ingest_job,
    run_github_repos_search,
)
from src.v2.indices.github_organizations import (
    run_github_orgs_ingest_job,
    run_github_orgs_search,
)
from src.v2.indices.github_users import (
    run_github_users_ingest_job,
    run_github_users_search,
)
from src.v2.indices.huggingface_papers import (
    run_huggingface_papers_ingest_job,
    run_huggingface_papers_search,
)
from src.v2.indices.huggingface_datasets import (
    run_huggingface_datasets_ingest_job,
    run_huggingface_datasets_search,
)
from src.v2.indices.huggingface_models import (
    run_huggingface_models_ingest_job,
    run_huggingface_models_search,
)
from src.v2.indices.huggingface_organizations import (
    run_huggingface_organizations_ingest_job,
    run_huggingface_organizations_search,
)
from src.v2.indices.huggingface_spaces import (
    run_huggingface_spaces_ingest_job,
    run_huggingface_spaces_search,
)
from src.v2.indices.huggingface_users import (
    run_huggingface_users_ingest_job,
    run_huggingface_users_search,
)
from src.v2.indices.jobs import IndexIngestJobStore
from src.v2.indices.oamonitor import (
    run_oamonitor_ingest_job,
    run_oamonitor_search,
)
from src.v2.indices.openalex import run_openalex_ingest_job, run_openalex_search
from src.v2.indices.orcid import run_orcid_ingest_job, run_orcid_search
from src.v2.indices.cli_catalogs import (
    run_communities_search,
    run_epfl_graph_search,
    run_infoscience_search,
    run_ror_search,
    run_snsf_search,
)
from src.v2.indices.compact import (
    CompactResult,
    close_cached_resources_for,
    compact_duckdb,
)
from src.v2.indices.renkulab import run_renkulab_ingest_job, run_renkulab_search
from src.v2.indices.stats import (
    INDEX_STATS_SUPPORTED_PROVIDERS,
    IndexStatsResponse,
    UnknownIndexProviderError,
    collect_index_stats,
    fetch_store_for_stats,
)
from src.v2.indices.swissubase import (
    run_swissubase_ingest_job,
    run_swissubase_search,
)
from src.v2.indices.zenodo_records import (
    run_zenodo_records_ingest_job,
    run_zenodo_records_search,
)
from src.v2.ingest.cache import ProviderCache
from src.v2.ingest.detection import UnsupportedGitHubURL, classify_github_url
from src.v2.jobs import JobStore
from src.v2.observation.github_rate_limit import (
    GitHubRateLimitSummary,
    probe_github_rate_limit,
)
from src.v2.observation.query_log import QueryLog, query_log_var
from src.v2.pipeline import PipelineOrchestrator
from src.v2.pipeline.stages import (
    AssembledOutput,
    RootEntityValidationError,
    apply_link_pruning_to_assembled_output,
    assemble_output,
    build_json_output,
    build_jsonld_output,
    compute_stats,
    guarantee_repo_author,
    infer_github_handle_parents,
    demote_github_props_to_units,
    emit_fork_parent_stubs,
    infer_article_source_organization,
    infer_org_units,
    tag_rule_based_disciplines,
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
    validate_articles,
    validate_author_classes,
    validate_ownership,
)
from src.v2.pipeline.stages.validate_org_github_handles import (
    validate_org_github_handles,
)
from src.v2.pipeline.stages.refine_with_llm import (
    is_enabled as _hybrid_refiner_is_enabled,
)
from src.v2.pipeline.stages.concept_tagging import (
    is_enabled as _concept_tagging_is_enabled,
)
from src.v2.pipeline.stages.concept_tagging import (
    resolve_backend as _resolve_concept_tagging_backend,
)
from src.v2.pipeline.stages.concept_tagging import (
    resolve_epfl_min_score as _resolve_concept_tagging_epfl_min_score,
)
from src.v2.pipeline.stages.concept_tagging import (
    resolve_related_enrichment as _resolve_concept_tagging_related_enrichment,
)
from src.v2.pipeline.stages.context_gather import RequiredProviderUnavailableError
from src.v2.schema import load_jsonld_context
from src.v2.validation import (
    SHACLValidator,
    StrictSchemaValidator,
    load_ontology_shapes_graph,
)
from src.v2.validation.shacl_validation import SHACLRuntimeUnavailableError

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

v2_router = APIRouter(prefix="/v2")


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


def _job_status_path(job_id: str) -> str:
    return f"/v2/jobs/{job_id}"


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
                    await asyncio.sleep(_JOB_HEARTBEAT_INTERVAL_SECONDS)
                except asyncio.CancelledError:
                    raise
                try:
                    current = job_store.get(job_id)
                    if current is None or current.status != V2ExtractJobStatus.RUNNING:
                        return
                    current.last_heartbeat_at = datetime.now(timezone.utc)
                    job_store.set(current)
                except Exception:  # noqa: BLE001
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
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            with contextlib.suppress(Exception):
                await heartbeat_task


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
            detected_path_kind=_extract_path_kind(full_path),
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
            detected_path_kind=_extract_path_kind(full_path),
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
    pipeline_cache_enabled = pipeline_cache is not None and _is_pipeline_cache_enabled()
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
        cached_response = pipeline_cache.get(pipeline_cache_key)
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
                    _format_duration(cached_seconds),
                )
                return response_model

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
    logger.info("%s: entity_count=%d", STAGE_PERMISSIVE_VALIDATION, permissive_entity_count)

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
            logger.exception("%s stage failed", STAGE_LLM_DEDUP)
            _append_unique_warning(warnings, f"llm_dedup stage failed: {exc}")
        else:
            typed_entity_buckets = dedup_result.typed_entity_buckets
            logger.info(
                "%s: accepted=%d rejected=%d remap=%d in %.2fs",
                STAGE_LLM_DEDUP,
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
        STAGE_RECONCILIATION,
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
    if _resolve_company_to_ror_enabled():
        stage_started_at = perf_counter()
        try:
            company_result = await run_resolve_company_to_ror_stage(
                reconciled=reconciled,
                provider=getattr(providers, "ror_rag", None),
            )
            logger.info(
                "%s: persons_examined=%d persons_resolved=%d "
                "memberships=%d organizations=%d "
                "queries=%d accepted=%d in %.2fs",
                STAGE_RESOLVE_COMPANY_TO_ROR,
                company_result.persons_examined,
                company_result.persons_resolved,
                company_result.memberships_created,
                company_result.organizations_created,
                company_result.queries_attempted,
                company_result.queries_accepted,
                perf_counter() - stage_started_at,
            )
        except Exception as exc:  # noqa: BLE001 — never fail the run on this stage
            logger.exception("%s stage failed", STAGE_RESOLVE_COMPANY_TO_ROR)
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
    if _resolve_bio_to_ror_enabled():
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
                STAGE_RESOLVE_BIO_TO_ROR,
                bio_result.persons_examined,
                bio_result.persons_resolved,
                bio_result.memberships_created,
                bio_result.organizations_created,
                bio_result.candidates_extracted,
                bio_result.queries_attempted,
                bio_result.queries_accepted,
                perf_counter() - stage_started_at,
            )
        except Exception as exc:  # noqa: BLE001 — never fail the run on this stage
            logger.exception("%s stage failed", STAGE_RESOLVE_BIO_TO_ROR)
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
        and _resolve_bio_to_ror_llm_enabled()
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
                STAGE_RESOLVE_BIO_TO_ROR_LLM,
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
        except Exception as exc:  # noqa: BLE001 — never fail the run on this stage
            logger.exception("%s stage failed", STAGE_RESOLVE_BIO_TO_ROR_LLM)
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
    if _resolve_placeholder_orgs_to_ror_enabled():
        stage_started_at = perf_counter()
        try:
            placeholder_result = await run_resolve_placeholder_orgs_to_ror_stage(
                reconciled=reconciled,
                provider=getattr(providers, "ror_rag", None),
            )
            logger.info(
                "%s: examined=%d resolved=%d memberships_rewritten=%d "
                "queries=%d accepted=%d in %.2fs",
                STAGE_RESOLVE_PLACEHOLDER_ORGS_TO_ROR,
                placeholder_result.placeholders_examined,
                placeholder_result.placeholders_resolved,
                placeholder_result.memberships_rewritten,
                placeholder_result.queries_attempted,
                placeholder_result.queries_accepted,
                perf_counter() - stage_started_at,
            )
        except Exception as exc:  # noqa: BLE001 — never fail the run on this stage
            logger.exception(
                "%s stage failed", STAGE_RESOLVE_PLACEHOLDER_ORGS_TO_ROR,
            )
            _append_unique_warning(
                warnings,
                f"resolve_placeholder_orgs_to_ror stage failed: {exc}",
            )

    apply_critic_pruning = _should_apply_critic_pruning()
    if resolved_runtime == AgentRuntime.LLM and not apply_critic_pruning:
        logger.info(
            "%s: skipped (V2_APPLY_CRITIC_PRUNING=false — entities preserved)",
            STAGE_LLM_CRITIC,
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
            logger.exception("%s stage failed", STAGE_LLM_CRITIC)
            _append_unique_warning(warnings, f"llm_critic stage failed: {exc}")
        else:
            reconciled = critic_result.reconciled
            critic_pruned_excluded_entities = critic_result.pruned_excluded_entities
            logger.info(
                "%s: proposed_drop=%d applied_drop=%d protected_roots=%d in %.2fs",
                STAGE_LLM_CRITIC,
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
            logger.exception("%s stage failed", STAGE_REFINE_WITH_LLM)
            _append_unique_warning(
                warnings,
                f"refine_with_llm stage failed: {exc}",
            )
        else:
            reconciled = refine_result.reconciled
            logger.info(
                "%s: refined=%d skipped=%d failed=%d in %.2fs",
                STAGE_REFINE_WITH_LLM,
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
        STAGE_STRICT_VALIDATION,
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
        logger.warning("%s: %s", STAGE_OUTPUT_ASSEMBLY, failure_message)
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
        logger.warning("%s: rootless output — %s", STAGE_OUTPUT_ASSEMBLY, exc)
        assembled_output = _build_rootless_assembled_output(
            reconciled=reconciled,
            strict_batch=strict_batch,
            root_warning=str(exc),
        )
    logger.info(
        "%s: related=%d excluded=%d warnings=%d in %.2fs",
        STAGE_OUTPUT_ASSEMBLY,
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
            STAGE_LINK_VERACITY,
            resolved_runtime.value,
        )
    elif not _is_link_veracity_enabled():
        logger.info(
            "%s: skipped (V2_LINK_VERACITY_ENABLED=false)",
            STAGE_LINK_VERACITY,
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
            logger.exception("%s stage failed", STAGE_LINK_VERACITY)
            _append_unique_warning(warnings, f"Link veracity stage failed: {exc}")
        else:
            logger.info(
                "%s: checked=%d supported=%d unsupported=%d failed=%d invalid_links=%d in %.2fs",
                STAGE_LINK_VERACITY,
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
                        STAGE_LINK_VERACITY,
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
        STAGE_JSONLD_BUILD,
        len(graph_nodes) if isinstance(graph_nodes, list) else 0,
        len(jsonld_context),
        perf_counter() - stage_started_at,
    )

    shacl_data_graph = _jsonld_to_graph(shacl_graph_payload)
    if shacl_data_graph is None:
        logger.warning("%s: skipped — unable to parse assembled graph payload", STAGE_SHACL_GATE)
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
            logger.warning("%s: skipped — %s", STAGE_SHACL_GATE, exc)
            _append_unique_warning(warnings, str(exc))
        except Exception as exc:
            logger.exception("%s failed", STAGE_SHACL_GATE)
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
                STAGE_SHACL_GATE,
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
    extract_graph = _jsonld_to_graph(output_payload) if output_format == "jsonld" else None
    final_entities = []
    if isinstance(assembled_output.root_entity, dict):
        final_entities.append(assembled_output.root_entity)
    final_entities.extend(assembled_output.related_entities)
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
        _format_duration(total_seconds),
        _format_duration(orchestrator_seconds),
        _format_duration(link_veracity_seconds),
        _format_duration(other_seconds),
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
    _maybe_schedule_github_repos_auto_ingest(
        classification=classification,
        run_id=run_id,
    )
    _maybe_schedule_github_users_auto_ingest(
        classification=classification,
        run_id=run_id,
    )
    _maybe_schedule_github_orgs_auto_ingest(
        classification=classification,
        run_id=run_id,
    )
    _maybe_schedule_huggingface_papers_auto_ingest(
        classification=classification,
        run_id=run_id,
    )

    return response_model


_GITHUB_REPOS_AUTO_INGEST_LOCK = threading.Lock()
_GITHUB_USERS_AUTO_INGEST_LOCK = threading.Lock()
_GITHUB_ORGS_AUTO_INGEST_LOCK = threading.Lock()
_HF_PAPERS_AUTO_INGEST_LOCK = threading.Lock()


def _hf_papers_arxiv_id_from_url(normalized_url: Any) -> str | None:
    """Extract an arXiv id from a `huggingface.co/papers/<arxiv_id>` URL.

    Only fires for HF Papers URLs — does NOT fire for raw `arxiv.org`
    URLs or arXiv DOIs, by design (the user opted in for HF Papers
    URLs specifically). Uses the canonical arXiv id normaliser so
    version suffixes are stripped.
    """
    if not isinstance(normalized_url, str) or "huggingface.co/papers/" not in normalized_url:
        return None
    try:
        from src.index.huggingface_papers.ingest.hf_papers_client import (  # noqa: PLC0415
            normalize_arxiv_id,
        )
    except Exception:  # noqa: BLE001
        return None
    return normalize_arxiv_id(normalized_url)


def _github_account_login_from_url(normalized_url: Any) -> str | None:
    """Extract a bare GitHub login from a normalised user/org URL, or None.

    Accepts the same URL shapes the classifier emits for user/org
    targets: `https://github.com/<login>` or `https://github.com/orgs/<login>`.
    Returns the bare handle on success, or None if the URL has the
    wrong host, an extra path segment (which would indicate a repo),
    or is malformed.
    """
    if not isinstance(normalized_url, str) or "github.com/" not in normalized_url:
        return None
    rest = (
        normalized_url.removeprefix("https://github.com/")
        .removeprefix("http://github.com/")
        .strip("/")
    )
    if not rest:
        return None
    # `/orgs/<login>` is GitHub's web UI URL for an org; strip the prefix.
    if rest.startswith("orgs/"):
        rest = rest[len("orgs/"):]
    # User/org URLs are a single path segment — anything with a `/`
    # left is a repo and shouldn't reach this helper.
    if "/" in rest:
        return None
    return rest or None


def _maybe_schedule_github_repos_auto_ingest(
    *,
    classification: Any,
    run_id: str,
) -> None:
    """Schedule a background GitHub RAG ingest when the operator opts in.

    Gates:
    - `V2_GITHUB_REPOS_RAG_AUTO_INGEST=true` env var (off by default — every
      existing deployment keeps its current behaviour).
    - The extract target must be a repository (we have no index for
      user/org/article cards yet).
    - `classification.normalized_url` must be a public github.com repo.
      Private/unreachable repos surface as `skipped_404` inside
      `ingest_single_repo` and emit one warning; no crash.

    Concurrency: a module-level `threading.Lock` serialises DuckDB
    writes across uvicorn worker tasks. The single-repo ingest is
    fast (~1-3s) so contention is negligible.
    """
    if os.getenv("V2_GITHUB_REPOS_RAG_AUTO_INGEST", "false").strip().lower() != "true":
        return
    if not hasattr(classification, "detected_type"):
        return
    if str(classification.detected_type.value).lower() != "repository":
        return
    normalized_url = getattr(classification, "normalized_url", None)
    if not isinstance(normalized_url, str) or "github.com/" not in normalized_url:
        return
    full_name = normalized_url.removeprefix("https://github.com/").removeprefix(
        "http://github.com/",
    ).strip("/")
    if not full_name or full_name.count("/") != 1:
        return

    async def _run() -> None:
        try:
            from src.index.github_repos.config import load_config as load_github_config  # noqa: PLC0415
            from src.index.github_repos.embed.pipeline import embed_repos  # noqa: PLC0415
            from src.index.github_repos.ingest.github_client import GitHubClient  # noqa: PLC0415
            from src.index.github_repos.ingest.repos import ingest_single_repo  # noqa: PLC0415
            from src.index.github_repos.storage.duckdb_store import GitHubReposStore  # noqa: PLC0415
        except Exception:  # noqa: BLE001
            logger.exception(
                "github auto-ingest (run_id=%s, repo=%s): module import failed",
                run_id, full_name,
            )
            return

        def _do_ingest() -> tuple[str, int]:
            cfg = load_github_config()
            cfg.require_github()
            with _GITHUB_REPOS_AUTO_INGEST_LOCK:
                store = GitHubReposStore.open(cfg.paths.duckdb_path)
                try:
                    existing = store.fetch_repo(full_name)
                    if existing is not None:
                        return ("skipped_already_indexed", 0)
                    client = GitHubClient(
                        api_base=cfg.github.api_base,
                        token=cfg.github.token,
                        cache_path=cfg.paths.cache_db_path,
                    )
                    outcome = ingest_single_repo(
                        config=cfg, store=store, client=client, full_name=full_name,
                    )
                    if outcome == "skipped_404":
                        return ("skipped_404", 0)
                    embed_summary = embed_repos(config=cfg, store=store, limit=None)
                    return (outcome, int(embed_summary.get("repos", 0)))
                finally:
                    store.close()

        try:
            outcome, embedded = await asyncio.to_thread(_do_ingest)
        except Exception:  # noqa: BLE001
            logger.exception(
                "github auto-ingest (run_id=%s, repo=%s): failed",
                run_id, full_name,
            )
            return
        logger.info(
            "github auto-ingest (run_id=%s, repo=%s): %s (chunks_embedded=%d)",
            run_id, full_name, outcome, embedded,
        )

    try:
        asyncio.create_task(_run())
    except RuntimeError:
        # No running event loop (e.g. unit tests that call extract
        # synchronously). Skip silently — the auto-ingest is a
        # non-essential background enrichment.
        return


def _maybe_schedule_github_users_auto_ingest(
    *,
    classification: Any,
    run_id: str,
) -> None:
    """Schedule a background ingest into the github_users index when
    the operator opts in via `V2_GITHUB_USERS_RAG_AUTO_INGEST=true`.

    Same gating shape as `_maybe_schedule_github_repos_auto_ingest` but
    fires only for `detected_type == "user"` targets. Org-typed
    extracts are handled by the sibling helper below.
    """
    if os.getenv("V2_GITHUB_USERS_RAG_AUTO_INGEST", "false").strip().lower() != "true":
        return
    if not hasattr(classification, "detected_type"):
        return
    if str(classification.detected_type.value).lower() != "user":
        return
    login = _github_account_login_from_url(
        getattr(classification, "normalized_url", None),
    )
    if login is None:
        return

    async def _run() -> None:
        try:
            from src.index.github_repos.ingest.github_client import GitHubClient  # noqa: PLC0415
            from src.index.github_users.config import load_config  # noqa: PLC0415
            from src.index.github_users.embed.pipeline import embed_users  # noqa: PLC0415
            from src.index.github_users.ingest.users import ingest_single_user  # noqa: PLC0415
            from src.index.github_users.storage.duckdb_store import (  # noqa: PLC0415
                GitHubUsersStore,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "github_users auto-ingest (run_id=%s, login=%s): module import failed",
                run_id, login,
            )
            return

        def _do_ingest() -> tuple[str, int]:
            cfg = load_config()
            cfg.require_github()
            with _GITHUB_USERS_AUTO_INGEST_LOCK:
                store = GitHubUsersStore.open(cfg.paths.duckdb_path)
                try:
                    existing = store.fetch_user(login)
                    if existing is not None:
                        return ("skipped_already_indexed", 0)
                    client = GitHubClient(
                        api_base=cfg.github.api_base,
                        token=cfg.github.token,
                        cache_path=cfg.paths.cache_db_path,
                    )
                    outcome = ingest_single_user(
                        config=cfg, store=store, client=client, login=login,
                    )
                    if outcome in {"skipped_404", "skipped_org"}:
                        return (outcome, 0)
                    embed_summary = embed_users(config=cfg, store=store, limit=None)
                    return (outcome, int(embed_summary.get("users", 0)))
                finally:
                    store.close()

        try:
            outcome, embedded = await asyncio.to_thread(_do_ingest)
        except Exception:  # noqa: BLE001
            logger.exception(
                "github_users auto-ingest (run_id=%s, login=%s): failed",
                run_id, login,
            )
            return
        logger.info(
            "github_users auto-ingest (run_id=%s, login=%s): %s (chunks_embedded=%d)",
            run_id, login, outcome, embedded,
        )

    try:
        asyncio.create_task(_run())
    except RuntimeError:
        return


def _maybe_schedule_github_orgs_auto_ingest(
    *,
    classification: Any,
    run_id: str,
) -> None:
    """Schedule a background ingest into the github_organizations index
    when `V2_GITHUB_ORGS_RAG_AUTO_INGEST=true`.

    Fires only for `detected_type == "organization"` targets — uses
    the v2 detected-type enum (`organization`, not `org`).
    """
    if os.getenv("V2_GITHUB_ORGS_RAG_AUTO_INGEST", "false").strip().lower() != "true":
        return
    if not hasattr(classification, "detected_type"):
        return
    if str(classification.detected_type.value).lower() != "organization":
        return
    login = _github_account_login_from_url(
        getattr(classification, "normalized_url", None),
    )
    if login is None:
        return

    async def _run() -> None:
        try:
            from src.index.github_repos.ingest.github_client import GitHubClient  # noqa: PLC0415
            from src.index.github_organizations.config import load_config  # noqa: PLC0415
            from src.index.github_organizations.embed.pipeline import (  # noqa: PLC0415
                embed_organizations,
            )
            from src.index.github_organizations.ingest.organizations import (  # noqa: PLC0415
                ingest_single_organization,
            )
            from src.index.github_organizations.storage.duckdb_store import (  # noqa: PLC0415
                GitHubOrganizationsStore,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "github_organizations auto-ingest (run_id=%s, login=%s): module import failed",
                run_id, login,
            )
            return

        def _do_ingest() -> tuple[str, int]:
            cfg = load_config()
            cfg.require_github()
            with _GITHUB_ORGS_AUTO_INGEST_LOCK:
                store = GitHubOrganizationsStore.open(cfg.paths.duckdb_path)
                try:
                    existing = store.fetch_organization(login)
                    if existing is not None:
                        return ("skipped_already_indexed", 0)
                    client = GitHubClient(
                        api_base=cfg.github.api_base,
                        token=cfg.github.token,
                        cache_path=cfg.paths.cache_db_path,
                    )
                    outcome = ingest_single_organization(
                        config=cfg, store=store, client=client, login=login,
                    )
                    if outcome in {"skipped_404", "skipped_user"}:
                        return (outcome, 0)
                    embed_summary = embed_organizations(
                        config=cfg, store=store, limit=None,
                    )
                    return (outcome, int(embed_summary.get("organizations", 0)))
                finally:
                    store.close()

        try:
            outcome, embedded = await asyncio.to_thread(_do_ingest)
        except Exception:  # noqa: BLE001
            logger.exception(
                "github_organizations auto-ingest (run_id=%s, login=%s): failed",
                run_id, login,
            )
            return
        logger.info(
            "github_organizations auto-ingest (run_id=%s, login=%s): %s (chunks_embedded=%d)",
            run_id, login, outcome, embedded,
        )

    try:
        asyncio.create_task(_run())
    except RuntimeError:
        return


def _maybe_schedule_huggingface_papers_auto_ingest(
    *,
    classification: Any,
    run_id: str,
) -> None:
    """Schedule a background ingest into the huggingface_papers index
    when `V2_HF_PAPERS_RAG_AUTO_INGEST=true` AND the extract target
    is a `huggingface.co/papers/<arxiv_id>` URL.

    Unlike the github_users / github_organizations helpers, this one
    does NOT check `classification.detected_type` — HF Papers URLs
    don't necessarily have a dedicated detected_type, so we gate
    purely on the URL pattern. The narrow URL match is the safety
    net: only true HF Papers URLs trigger; raw arXiv URLs and DOIs
    are skipped (per the operator's choice when this feature was
    designed).
    """
    if os.getenv("V2_HF_PAPERS_RAG_AUTO_INGEST", "false").strip().lower() != "true":
        return
    if not hasattr(classification, "normalized_url"):
        return
    arxiv_id = _hf_papers_arxiv_id_from_url(
        getattr(classification, "normalized_url", None),
    )
    if arxiv_id is None:
        return

    async def _run() -> None:
        try:
            from src.index.huggingface_papers.config import load_config  # noqa: PLC0415
            from src.index.huggingface_papers.embed.pipeline import embed_papers  # noqa: PLC0415
            from src.index.huggingface_papers.ingest.hf_papers_client import (  # noqa: PLC0415
                HFPapersClient,
            )
            from src.index.huggingface_papers.ingest.papers import (  # noqa: PLC0415
                ingest_single_paper,
            )
            from src.index.huggingface_papers.storage.duckdb_store import (  # noqa: PLC0415
                HuggingFacePapersStore,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "huggingface_papers auto-ingest (run_id=%s, arxiv_id=%s): module import failed",
                run_id, arxiv_id,
            )
            return

        def _do_ingest() -> tuple[str, int]:
            cfg = load_config()
            with _HF_PAPERS_AUTO_INGEST_LOCK:
                store = HuggingFacePapersStore.open(cfg.paths.duckdb_path)
                try:
                    existing = store.fetch_paper(arxiv_id)
                    if existing is not None:
                        return ("skipped_already_indexed", 0)
                    client = HFPapersClient(
                        api_base=cfg.huggingface.api_base,
                        token=cfg.huggingface.token,
                        cache_path=cfg.paths.cache_db_path,
                    )
                    outcome = ingest_single_paper(
                        config=cfg, store=store, client=client, arxiv_id=arxiv_id,
                    )
                    if outcome == "skipped_404":
                        return (outcome, 0)
                    embed_summary = embed_papers(config=cfg, store=store, limit=None)
                    return (outcome, int(embed_summary.get("papers", 0)))
                finally:
                    store.close()

        try:
            outcome, embedded = await asyncio.to_thread(_do_ingest)
        except Exception:  # noqa: BLE001
            logger.exception(
                "huggingface_papers auto-ingest (run_id=%s, arxiv_id=%s): failed",
                run_id, arxiv_id,
            )
            return
        logger.info(
            "huggingface_papers auto-ingest (run_id=%s, arxiv_id=%s): %s (chunks_embedded=%d)",
            run_id, arxiv_id, outcome, embedded,
        )

    try:
        asyncio.create_task(_run())
    except RuntimeError:
        return


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
            detected_path_kind=_extract_path_kind(payload.source_url),
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
            detected_path_kind=_extract_path_kind(payload.source_url),
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=error_payload.model_dump(mode="json", exclude_none=True),
        )

    job_store = _resolve_job_store(request)
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
    _track_background_task(request, task)

    logger.info(
        "extract job submitted: job_id=%s url=%s detected_type=%s",
        job_id,
        classification.normalized_url,
        classification.detected_type.value,
    )
    return V2ExtractJobAccepted(
        job_id=job_id,
        status=V2ExtractJobStatus.PENDING,
        status_url=_job_status_path(job_id),
        submitted_at=submitted_at,
    )


@v2_router.get(
    "/jobs/{job_id}",
    response_model=V2ExtractJob,
    response_model_exclude_none=True,
)
async def extract_job(
    job_id: Annotated[str, Path(description="Job id returned by POST /v2/extract.")],
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> V2ExtractJob | JSONResponse:
    """Retrieve a previously submitted extraction job by id."""

    job_store = _resolve_job_store(request)
    if job_store is None:
        error_payload = V2ErrorResponse(
            error_type=V2ErrorType.PIPELINE_ERROR,
            detail="async job store unavailable: provider cache is disabled",
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=error_payload.model_dump(mode="json", exclude_none=True),
        )

    record = job_store.get(job_id)
    if record is None:
        error_payload = V2ErrorResponse(
            error_type=V2ErrorType.NOT_FOUND,
            detail=f"no extract job found with id '{job_id}'",
        )
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=error_payload.model_dump(mode="json", exclude_none=True),
        )
    # Detect orphaned jobs: the worker that was executing this job died
    # (OS kill, deploy, OOM, …) without flipping the status, so the
    # stored record is stuck in RUNNING. We notice because no heartbeat
    # has been written in over `_JOB_STALE_THRESHOLD_SECONDS`. Flip to
    # FAILED, persist, and return the failed view so the client stops
    # polling. New `POST /v2/extract` calls will start a fresh job.
    if record.status == V2ExtractJobStatus.RUNNING:
        now = datetime.now(timezone.utc)
        beat = record.last_heartbeat_at or record.started_at or record.submitted_at
        if beat is not None and (now - beat).total_seconds() > _JOB_STALE_THRESHOLD_SECONDS:
            stale_seconds = (now - beat).total_seconds()
            logger.warning(
                "marking job %s as FAILED: no heartbeat for %.0fs "
                "(threshold=%.0fs) — worker likely died mid-flight",
                job_id,
                stale_seconds,
                _JOB_STALE_THRESHOLD_SECONDS,
            )
            record.status = V2ExtractJobStatus.FAILED
            record.completed_at = now
            record.error = V2ErrorResponse(
                error_type=V2ErrorType.PIPELINE_ERROR,
                detail=(
                    "extract job orphaned: worker process died mid-extraction "
                    f"(no heartbeat for {int(stale_seconds)}s)"
                ),
                source_url=record.request.source_url,
            )
            job_store.set(record)
    return record


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
    sub-caches (RAG, Selenium, link veracity, etc.). The v1 cache at
    `/v1/cache/clear` is a separate store and is not touched here.
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


# --- /v2/indices/<name>/ingest --------------------------------------------
# Async ingestion routes for the RAG indices. Each POST enqueues a job in the
# shared `IndexIngestJobStore` and dispatches the heavy work to a background
# task; clients poll `GET /v2/indices/jobs/{job_id}` for the outcome.


def _resolve_index_ingest_job_store(request: Request) -> IndexIngestJobStore | None:
    cache = _resolve_provider_cache(request.app.state)
    if not isinstance(cache, ProviderCache):
        return None
    return IndexIngestJobStore(cache)


def _index_job_status_path(job_id: str) -> str:
    return f"/v2/indices/jobs/{job_id}"


@v2_router.post(
    "/indices/zenodo_records/ingest",
    response_model=IndexIngestJobAccepted,
    response_model_exclude_none=True,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Indices"],
)
async def zenodo_ingest_post(
    payload: ZenodoIngestRequest,
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexIngestJobAccepted | JSONResponse:
    """Enqueue a Zenodo ingest for one or more record ids."""

    job_store = _resolve_index_ingest_job_store(request)
    if job_store is None:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": "index ingest job store unavailable: provider cache is disabled",
            },
        )

    job_id = str(uuid4())
    submitted_at = datetime.now(timezone.utc)
    job = IndexIngestJob(
        job_id=job_id,
        index_name="zenodo_records",
        status=IndexIngestJobStatus.PENDING,
        request=payload.model_dump(mode="json"),
        submitted_at=submitted_at,
    )
    job_store.set(job)

    task = asyncio.create_task(
        run_zenodo_records_ingest_job(
            payload=payload,
            app_state=request.app.state,
            job_store=job_store,
            job_id=job_id,
        ),
    )
    _track_background_task(request, task)

    logger.info(
        "zenodo ingest job submitted: job_id=%s ids=%d refresh=%s",
        job_id,
        len(payload.ids),
        payload.refresh,
    )
    return IndexIngestJobAccepted(
        job_id=job_id,
        index_name="zenodo_records",
        status=IndexIngestJobStatus.PENDING,
        status_url=_index_job_status_path(job_id),
        submitted_at=submitted_at,
    )


def _hf_entity_ingest_post(
    request: Request,
    *,
    index_name: str,
    payload: Any,
    runner: Any,
    item_count: int,
) -> IndexIngestJobAccepted | JSONResponse:
    """Shared body for the five per-entity HF ingest endpoints. Each
    POST handler delegates here after pulling its typed payload + the
    matching `run_*_ingest_job` coroutine."""
    job_store = _resolve_index_ingest_job_store(request)
    if job_store is None:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": "index ingest job store unavailable: provider cache is disabled",
            },
        )
    job_id = str(uuid4())
    submitted_at = datetime.now(timezone.utc)
    job = IndexIngestJob(
        job_id=job_id,
        index_name=index_name,
        status=IndexIngestJobStatus.PENDING,
        request=payload.model_dump(mode="json"),
        submitted_at=submitted_at,
    )
    job_store.set(job)
    task = asyncio.create_task(
        runner(
            payload=payload,
            app_state=request.app.state,
            job_store=job_store,
            job_id=job_id,
        ),
    )
    _track_background_task(request, task)
    logger.info(
        "%s ingest job submitted: job_id=%s items=%d",
        index_name, job_id, item_count,
    )
    return IndexIngestJobAccepted(
        job_id=job_id,
        index_name=index_name,
        status=IndexIngestJobStatus.PENDING,
        status_url=_index_job_status_path(job_id),
        submitted_at=submitted_at,
    )


@v2_router.post(
    "/indices/huggingface_models/ingest",
    response_model=IndexIngestJobAccepted,
    response_model_exclude_none=True,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Indices"],
)
async def huggingface_models_ingest_post(
    payload: HuggingFaceModelsIngestRequest,
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexIngestJobAccepted | JSONResponse:
    """Enqueue a HuggingFace models ingest for one or more repo_ids."""
    return _hf_entity_ingest_post(
        request,
        index_name="huggingface_models",
        payload=payload,
        runner=run_huggingface_models_ingest_job,
        item_count=len(payload.repo_ids),
    )


@v2_router.post(
    "/indices/huggingface_datasets/ingest",
    response_model=IndexIngestJobAccepted,
    response_model_exclude_none=True,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Indices"],
)
async def huggingface_datasets_ingest_post(
    payload: HuggingFaceDatasetsIngestRequest,
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexIngestJobAccepted | JSONResponse:
    """Enqueue a HuggingFace datasets ingest for one or more repo_ids."""
    return _hf_entity_ingest_post(
        request,
        index_name="huggingface_datasets",
        payload=payload,
        runner=run_huggingface_datasets_ingest_job,
        item_count=len(payload.repo_ids),
    )


@v2_router.post(
    "/indices/huggingface_spaces/ingest",
    response_model=IndexIngestJobAccepted,
    response_model_exclude_none=True,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Indices"],
)
async def huggingface_spaces_ingest_post(
    payload: HuggingFaceSpacesIngestRequest,
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexIngestJobAccepted | JSONResponse:
    """Enqueue a HuggingFace spaces ingest for one or more repo_ids."""
    return _hf_entity_ingest_post(
        request,
        index_name="huggingface_spaces",
        payload=payload,
        runner=run_huggingface_spaces_ingest_job,
        item_count=len(payload.repo_ids),
    )


@v2_router.post(
    "/indices/huggingface_users/ingest",
    response_model=IndexIngestJobAccepted,
    response_model_exclude_none=True,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Indices"],
)
async def huggingface_users_ingest_post(
    payload: HuggingFaceUsersIngestRequest,
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexIngestJobAccepted | JSONResponse:
    """Enqueue a HuggingFace users ingest for one or more namespace slugs."""
    return _hf_entity_ingest_post(
        request,
        index_name="huggingface_users",
        payload=payload,
        runner=run_huggingface_users_ingest_job,
        item_count=len(payload.slugs),
    )


@v2_router.post(
    "/indices/huggingface_organizations/ingest",
    response_model=IndexIngestJobAccepted,
    response_model_exclude_none=True,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Indices"],
)
async def huggingface_organizations_ingest_post(
    payload: HuggingFaceOrganizationsIngestRequest,
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexIngestJobAccepted | JSONResponse:
    """Enqueue a HuggingFace organizations ingest for one or more namespace slugs."""
    return _hf_entity_ingest_post(
        request,
        index_name="huggingface_organizations",
        payload=payload,
        runner=run_huggingface_organizations_ingest_job,
        item_count=len(payload.slugs),
    )


@v2_router.post(
    "/indices/github_repos/ingest",
    response_model=IndexIngestJobAccepted,
    response_model_exclude_none=True,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Indices"],
)
async def github_ingest_post(
    payload: GitHubIngestRequest,
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexIngestJobAccepted | JSONResponse:
    """Enqueue a GitHub ingest for one or more `owner/name` repo handles."""

    job_store = _resolve_index_ingest_job_store(request)
    if job_store is None:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": "index ingest job store unavailable: provider cache is disabled",
            },
        )
    job_id = str(uuid4())
    submitted_at = datetime.now(timezone.utc)
    job = IndexIngestJob(
        job_id=job_id, index_name="github_repos", status=IndexIngestJobStatus.PENDING,
        request=payload.model_dump(mode="json"), submitted_at=submitted_at,
    )
    job_store.set(job)
    task = asyncio.create_task(
        run_github_repos_ingest_job(
            payload=payload, app_state=request.app.state,
            job_store=job_store, job_id=job_id,
        ),
    )
    _track_background_task(request, task)
    logger.info("github ingest job submitted: job_id=%s repos=%d", job_id, len(payload.repos))
    return IndexIngestJobAccepted(
        job_id=job_id, index_name="github_repos", status=IndexIngestJobStatus.PENDING,
        status_url=_index_job_status_path(job_id), submitted_at=submitted_at,
    )


@v2_router.post(
    "/indices/github_users/ingest",
    response_model=IndexIngestJobAccepted,
    response_model_exclude_none=True,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Indices"],
)
async def github_users_ingest_post(
    payload: GitHubUsersIngestRequest,
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexIngestJobAccepted | JSONResponse:
    """Enqueue a github_users ingest for one or more user logins.

    Each login is fetched via `GET /users/{login}` and persisted to the
    github_users DuckDB + Qdrant collection. Org-typed payloads are
    skipped (they belong in github_organizations).
    """
    job_store = _resolve_index_ingest_job_store(request)
    if job_store is None:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": "index ingest job store unavailable: provider cache is disabled",
            },
        )
    job_id = str(uuid4())
    submitted_at = datetime.now(timezone.utc)
    job = IndexIngestJob(
        job_id=job_id, index_name="github_users", status=IndexIngestJobStatus.PENDING,
        request=payload.model_dump(mode="json"), submitted_at=submitted_at,
    )
    job_store.set(job)
    task = asyncio.create_task(
        run_github_users_ingest_job(
            payload=payload, app_state=request.app.state,
            job_store=job_store, job_id=job_id,
        ),
    )
    _track_background_task(request, task)
    logger.info(
        "github_users ingest job submitted: job_id=%s logins=%d",
        job_id, len(payload.logins),
    )
    return IndexIngestJobAccepted(
        job_id=job_id, index_name="github_users",
        status=IndexIngestJobStatus.PENDING,
        status_url=_index_job_status_path(job_id), submitted_at=submitted_at,
    )


@v2_router.post(
    "/indices/github_organizations/ingest",
    response_model=IndexIngestJobAccepted,
    response_model_exclude_none=True,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Indices"],
)
async def github_organizations_ingest_post(
    payload: GitHubOrgsIngestRequest,
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexIngestJobAccepted | JSONResponse:
    """Enqueue a github_organizations ingest for one or more org handles.

    Each handle is fetched via `GET /orgs/{org}` and persisted to the
    github_organizations DuckDB + Qdrant collection.
    """
    job_store = _resolve_index_ingest_job_store(request)
    if job_store is None:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": "index ingest job store unavailable: provider cache is disabled",
            },
        )
    job_id = str(uuid4())
    submitted_at = datetime.now(timezone.utc)
    job = IndexIngestJob(
        job_id=job_id, index_name="github_organizations",
        status=IndexIngestJobStatus.PENDING,
        request=payload.model_dump(mode="json"), submitted_at=submitted_at,
    )
    job_store.set(job)
    task = asyncio.create_task(
        run_github_orgs_ingest_job(
            payload=payload, app_state=request.app.state,
            job_store=job_store, job_id=job_id,
        ),
    )
    _track_background_task(request, task)
    logger.info(
        "github_organizations ingest job submitted: job_id=%s orgs=%d",
        job_id, len(payload.orgs),
    )
    return IndexIngestJobAccepted(
        job_id=job_id, index_name="github_organizations",
        status=IndexIngestJobStatus.PENDING,
        status_url=_index_job_status_path(job_id), submitted_at=submitted_at,
    )


@v2_router.post(
    "/indices/huggingface_papers/ingest",
    response_model=IndexIngestJobAccepted,
    response_model_exclude_none=True,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Indices"],
)
async def huggingface_papers_ingest_post(
    payload: HuggingFacePapersIngestRequest,
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexIngestJobAccepted | JSONResponse:
    """Enqueue a huggingface_papers ingest for one or more arXiv ids.

    Each id can arrive in any wire shape — bare (`2310.01234`), with
    version suffix, as an arXiv or HF Papers URL, as an `arxiv:` tag,
    or as an arXiv DOI. The job normaliser strips to the canonical id
    before fetch.
    """
    job_store = _resolve_index_ingest_job_store(request)
    if job_store is None:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": "index ingest job store unavailable: provider cache is disabled",
            },
        )
    job_id = str(uuid4())
    submitted_at = datetime.now(timezone.utc)
    job = IndexIngestJob(
        job_id=job_id, index_name="huggingface_papers",
        status=IndexIngestJobStatus.PENDING,
        request=payload.model_dump(mode="json"), submitted_at=submitted_at,
    )
    job_store.set(job)
    task = asyncio.create_task(
        run_huggingface_papers_ingest_job(
            payload=payload, app_state=request.app.state,
            job_store=job_store, job_id=job_id,
        ),
    )
    _track_background_task(request, task)
    logger.info(
        "huggingface_papers ingest job submitted: job_id=%s arxiv_ids=%d",
        job_id, len(payload.arxiv_ids),
    )
    return IndexIngestJobAccepted(
        job_id=job_id, index_name="huggingface_papers",
        status=IndexIngestJobStatus.PENDING,
        status_url=_index_job_status_path(job_id), submitted_at=submitted_at,
    )


@v2_router.post(
    "/indices/openalex/ingest",
    response_model=IndexIngestJobAccepted,
    response_model_exclude_none=True,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Indices"],
)
async def openalex_ingest_post(
    payload: OpenAlexIngestRequest,
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexIngestJobAccepted | JSONResponse:
    """Enqueue an OpenAlex ingest for one or more work IDs / URLs / DOIs."""

    job_store = _resolve_index_ingest_job_store(request)
    if job_store is None:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": "index ingest job store unavailable: provider cache is disabled",
            },
        )
    job_id = str(uuid4())
    submitted_at = datetime.now(timezone.utc)
    job = IndexIngestJob(
        job_id=job_id, index_name="openalex", status=IndexIngestJobStatus.PENDING,
        request=payload.model_dump(mode="json"), submitted_at=submitted_at,
    )
    job_store.set(job)
    task = asyncio.create_task(
        run_openalex_ingest_job(
            payload=payload, app_state=request.app.state,
            job_store=job_store, job_id=job_id,
        ),
    )
    _track_background_task(request, task)
    logger.info("openalex ingest job submitted: job_id=%s ids=%d", job_id, len(payload.ids))
    return IndexIngestJobAccepted(
        job_id=job_id, index_name="openalex", status=IndexIngestJobStatus.PENDING,
        status_url=_index_job_status_path(job_id), submitted_at=submitted_at,
    )


@v2_router.post(
    "/indices/orcid/ingest",
    response_model=IndexIngestJobAccepted,
    response_model_exclude_none=True,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Indices"],
)
async def orcid_ingest_post(
    payload: OrcidIngestRequest,
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexIngestJobAccepted | JSONResponse:
    """Enqueue an ORCID ingest for one or more ORCID identifiers."""

    job_store = _resolve_index_ingest_job_store(request)
    if job_store is None:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": "index ingest job store unavailable: provider cache is disabled",
            },
        )
    job_id = str(uuid4())
    submitted_at = datetime.now(timezone.utc)
    job = IndexIngestJob(
        job_id=job_id, index_name="orcid", status=IndexIngestJobStatus.PENDING,
        request=payload.model_dump(mode="json"), submitted_at=submitted_at,
    )
    job_store.set(job)
    task = asyncio.create_task(
        run_orcid_ingest_job(
            payload=payload, app_state=request.app.state,
            job_store=job_store, job_id=job_id,
        ),
    )
    _track_background_task(request, task)
    logger.info("orcid ingest job submitted: job_id=%s ids=%d", job_id, len(payload.orcid_ids))
    return IndexIngestJobAccepted(
        job_id=job_id, index_name="orcid", status=IndexIngestJobStatus.PENDING,
        status_url=_index_job_status_path(job_id), submitted_at=submitted_at,
    )


@v2_router.post(
    "/indices/renkulab/ingest",
    response_model=IndexIngestJobAccepted,
    response_model_exclude_none=True,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Indices"],
)
async def renkulab_ingest_post(
    payload: RenkulabIngestRequest,
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexIngestJobAccepted | JSONResponse:
    """Enqueue a Renkulab ingest for one or more project ids (UUID or namespace/slug)."""

    job_store = _resolve_index_ingest_job_store(request)
    if job_store is None:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": "index ingest job store unavailable: provider cache is disabled",
            },
        )
    job_id = str(uuid4())
    submitted_at = datetime.now(timezone.utc)
    job = IndexIngestJob(
        job_id=job_id, index_name="renkulab", status=IndexIngestJobStatus.PENDING,
        request=payload.model_dump(mode="json"), submitted_at=submitted_at,
    )
    job_store.set(job)
    task = asyncio.create_task(
        run_renkulab_ingest_job(
            payload=payload, app_state=request.app.state,
            job_store=job_store, job_id=job_id,
        ),
    )
    _track_background_task(request, task)
    logger.info(
        "renkulab ingest job submitted: job_id=%s project_ids=%d",
        job_id, len(payload.project_ids),
    )
    return IndexIngestJobAccepted(
        job_id=job_id, index_name="renkulab", status=IndexIngestJobStatus.PENDING,
        status_url=_index_job_status_path(job_id), submitted_at=submitted_at,
    )


@v2_router.post(
    "/indices/swissubase/ingest",
    response_model=IndexIngestJobAccepted,
    response_model_exclude_none=True,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Indices"],
)
async def swissubase_ingest_post(
    payload: SwissubaseIngestRequest,
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexIngestJobAccepted | JSONResponse:
    """Enqueue a SWISSUbase ingest for one or more numeric study ids."""

    job_store = _resolve_index_ingest_job_store(request)
    if job_store is None:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": "index ingest job store unavailable: provider cache is disabled",
            },
        )
    job_id = str(uuid4())
    submitted_at = datetime.now(timezone.utc)
    job = IndexIngestJob(
        job_id=job_id, index_name="swissubase", status=IndexIngestJobStatus.PENDING,
        request=payload.model_dump(mode="json"), submitted_at=submitted_at,
    )
    job_store.set(job)
    task = asyncio.create_task(
        run_swissubase_ingest_job(
            payload=payload, app_state=request.app.state,
            job_store=job_store, job_id=job_id,
        ),
    )
    _track_background_task(request, task)
    logger.info(
        "swissubase ingest job submitted: job_id=%s study_ids=%d",
        job_id, len(payload.study_ids),
    )
    return IndexIngestJobAccepted(
        job_id=job_id, index_name="swissubase", status=IndexIngestJobStatus.PENDING,
        status_url=_index_job_status_path(job_id), submitted_at=submitted_at,
    )


@v2_router.post(
    "/indices/ethz_research_collection/ingest",
    response_model=IndexIngestJobAccepted,
    response_model_exclude_none=True,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Indices"],
)
async def ethz_research_collection_ingest_post(
    payload: EthzResearchCollectionIngestRequest,
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexIngestJobAccepted | JSONResponse:
    """Enqueue an ETH Research Collection ingest for one or more item UUIDs."""

    job_store = _resolve_index_ingest_job_store(request)
    if job_store is None:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": "index ingest job store unavailable: provider cache is disabled",
            },
        )
    job_id = str(uuid4())
    submitted_at = datetime.now(timezone.utc)
    job = IndexIngestJob(
        job_id=job_id, index_name="ethz_research_collection",
        status=IndexIngestJobStatus.PENDING,
        request=payload.model_dump(mode="json"), submitted_at=submitted_at,
    )
    job_store.set(job)
    task = asyncio.create_task(
        run_ethz_research_collection_ingest_job(
            payload=payload, app_state=request.app.state,
            job_store=job_store, job_id=job_id,
        ),
    )
    _track_background_task(request, task)
    logger.info(
        "ethz_research_collection ingest job submitted: job_id=%s uuids=%d",
        job_id, len(payload.uuids),
    )
    return IndexIngestJobAccepted(
        job_id=job_id, index_name="ethz_research_collection",
        status=IndexIngestJobStatus.PENDING,
        status_url=_index_job_status_path(job_id), submitted_at=submitted_at,
    )


@v2_router.post(
    "/indices/oamonitor/ingest",
    response_model=IndexIngestJobAccepted,
    response_model_exclude_none=True,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Indices"],
)
async def oamonitor_ingest_post(
    payload: OamonitorIngestRequest,
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexIngestJobAccepted | JSONResponse:
    """Enqueue an OAM-CH ingest for one or more `{entity, id}` items."""

    job_store = _resolve_index_ingest_job_store(request)
    if job_store is None:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": "index ingest job store unavailable: provider cache is disabled",
            },
        )
    job_id = str(uuid4())
    submitted_at = datetime.now(timezone.utc)
    job = IndexIngestJob(
        job_id=job_id, index_name="oamonitor",
        status=IndexIngestJobStatus.PENDING,
        request=payload.model_dump(mode="json"), submitted_at=submitted_at,
    )
    job_store.set(job)
    task = asyncio.create_task(
        run_oamonitor_ingest_job(
            payload=payload, app_state=request.app.state,
            job_store=job_store, job_id=job_id,
        ),
    )
    _track_background_task(request, task)
    logger.info(
        "oamonitor ingest job submitted: job_id=%s items=%d",
        job_id, len(payload.items),
    )
    return IndexIngestJobAccepted(
        job_id=job_id, index_name="oamonitor",
        status=IndexIngestJobStatus.PENDING,
        status_url=_index_job_status_path(job_id), submitted_at=submitted_at,
    )


@v2_router.post(
    "/indices/dockerhub/ingest",
    response_model=IndexIngestJobAccepted,
    response_model_exclude_none=True,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Indices"],
)
async def dockerhub_ingest_post(
    payload: DockerhubIngestRequest,
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexIngestJobAccepted | JSONResponse:
    """Enqueue a Docker Hub ingest for one or more image references."""

    job_store = _resolve_index_ingest_job_store(request)
    if job_store is None:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": "index ingest job store unavailable: provider cache is disabled",
            },
        )
    job_id = str(uuid4())
    submitted_at = datetime.now(timezone.utc)
    job = IndexIngestJob(
        job_id=job_id, index_name="dockerhub", status=IndexIngestJobStatus.PENDING,
        request=payload.model_dump(mode="json"), submitted_at=submitted_at,
    )
    job_store.set(job)
    task = asyncio.create_task(
        run_dockerhub_ingest_job(
            payload=payload, app_state=request.app.state,
            job_store=job_store, job_id=job_id,
        ),
    )
    _track_background_task(request, task)
    logger.info(
        "dockerhub ingest job submitted: job_id=%s images=%d",
        job_id, len(payload.images),
    )
    return IndexIngestJobAccepted(
        job_id=job_id, index_name="dockerhub", status=IndexIngestJobStatus.PENDING,
        status_url=_index_job_status_path(job_id), submitted_at=submitted_at,
    )


async def _search_response_or_unavailable(
    response: IndexSearchResponse | None, *, index_name: str,
) -> IndexSearchResponse | JSONResponse:
    if response is None:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": f"{index_name} index module unavailable on this deployment",
            },
        )
    return response


@v2_router.post(
    "/indices/zenodo_records/search",
    response_model=IndexSearchResponse,
    response_model_exclude_none=True,
    tags=["Indices"],
)
async def zenodo_search_post(
    payload: IndexSearchRequest,
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexSearchResponse | JSONResponse:
    """Semantic search against the Zenodo index."""
    return await _search_response_or_unavailable(
        await run_zenodo_records_search(payload, request.app.state), index_name="zenodo_records",
    )


@v2_router.post(
    "/indices/huggingface_models/search",
    response_model=IndexSearchResponse,
    response_model_exclude_none=True,
    tags=["Indices"],
)
async def huggingface_models_search_post(
    payload: IndexSearchRequest,
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexSearchResponse | JSONResponse:
    """Semantic search against the huggingface_models index."""
    return await _search_response_or_unavailable(
        await run_huggingface_models_search(payload, request.app.state),
        index_name="huggingface_models",
    )


@v2_router.post(
    "/indices/huggingface_datasets/search",
    response_model=IndexSearchResponse,
    response_model_exclude_none=True,
    tags=["Indices"],
)
async def huggingface_datasets_search_post(
    payload: IndexSearchRequest,
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexSearchResponse | JSONResponse:
    """Semantic search against the huggingface_datasets index."""
    return await _search_response_or_unavailable(
        await run_huggingface_datasets_search(payload, request.app.state),
        index_name="huggingface_datasets",
    )


@v2_router.post(
    "/indices/huggingface_spaces/search",
    response_model=IndexSearchResponse,
    response_model_exclude_none=True,
    tags=["Indices"],
)
async def huggingface_spaces_search_post(
    payload: IndexSearchRequest,
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexSearchResponse | JSONResponse:
    """Semantic search against the huggingface_spaces index."""
    return await _search_response_or_unavailable(
        await run_huggingface_spaces_search(payload, request.app.state),
        index_name="huggingface_spaces",
    )


@v2_router.post(
    "/indices/huggingface_users/search",
    response_model=IndexSearchResponse,
    response_model_exclude_none=True,
    tags=["Indices"],
)
async def huggingface_users_search_post(
    payload: IndexSearchRequest,
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexSearchResponse | JSONResponse:
    """Semantic search against the huggingface_users index."""
    return await _search_response_or_unavailable(
        await run_huggingface_users_search(payload, request.app.state),
        index_name="huggingface_users",
    )


@v2_router.post(
    "/indices/huggingface_organizations/search",
    response_model=IndexSearchResponse,
    response_model_exclude_none=True,
    tags=["Indices"],
)
async def huggingface_organizations_search_post(
    payload: IndexSearchRequest,
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexSearchResponse | JSONResponse:
    """Semantic search against the huggingface_organizations index."""
    return await _search_response_or_unavailable(
        await run_huggingface_organizations_search(payload, request.app.state),
        index_name="huggingface_organizations",
    )


@v2_router.post(
    "/indices/github_repos/search",
    response_model=IndexSearchResponse,
    response_model_exclude_none=True,
    tags=["Indices"],
)
async def github_search_post(
    payload: IndexSearchRequest,
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexSearchResponse | JSONResponse:
    """Semantic search against the GitHub repos index."""
    return await _search_response_or_unavailable(
        await run_github_repos_search(payload, request.app.state), index_name="github_repos",
    )


@v2_router.post(
    "/indices/github_users/search",
    response_model=IndexSearchResponse,
    response_model_exclude_none=True,
    tags=["Indices"],
)
async def github_users_search_post(
    payload: IndexSearchRequest,
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexSearchResponse | JSONResponse:
    """Semantic search against the github_users index.

    Returns user cards ordered by relevance to the query — useful for
    disambiguating affiliations or finding a researcher by topic.
    """
    return await _search_response_or_unavailable(
        await run_github_users_search(payload, request.app.state),
        index_name="github_users",
    )


@v2_router.post(
    "/indices/github_organizations/search",
    response_model=IndexSearchResponse,
    response_model_exclude_none=True,
    tags=["Indices"],
)
async def github_organizations_search_post(
    payload: IndexSearchRequest,
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexSearchResponse | JSONResponse:
    """Semantic search against the github_organizations index."""
    return await _search_response_or_unavailable(
        await run_github_orgs_search(payload, request.app.state),
        index_name="github_organizations",
    )


@v2_router.post(
    "/indices/huggingface_papers/search",
    response_model=IndexSearchResponse,
    response_model_exclude_none=True,
    tags=["Indices"],
)
async def huggingface_papers_search_post(
    payload: IndexSearchRequest,
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexSearchResponse | JSONResponse:
    """Semantic search against the huggingface_papers index.

    Returns arXiv paper cards (HF-curated) ordered by relevance to
    the query. Useful for "find papers about X" queries grounded in
    the HF Papers daily feed and AI-summary metadata.
    """
    return await _search_response_or_unavailable(
        await run_huggingface_papers_search(payload, request.app.state),
        index_name="huggingface_papers",
    )


@v2_router.post(
    "/indices/openalex/search",
    response_model=IndexSearchResponse,
    response_model_exclude_none=True,
    tags=["Indices"],
)
async def openalex_search_post(
    payload: IndexSearchRequest,
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexSearchResponse | JSONResponse:
    """Semantic search against the OpenAlex index.

    Use ``target`` to pick the entity type: ``works`` (default), ``authors``,
    ``institutions``, ``sources``, ``topics``, ``concepts``.
    """
    return await _search_response_or_unavailable(
        await run_openalex_search(payload, request.app.state), index_name="openalex",
    )


@v2_router.post(
    "/indices/orcid/search",
    response_model=IndexSearchResponse,
    response_model_exclude_none=True,
    tags=["Indices"],
)
async def orcid_search_post(
    payload: IndexSearchRequest,
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexSearchResponse | JSONResponse:
    """Semantic search against the ORCID persons index."""
    return await _search_response_or_unavailable(
        await run_orcid_search(payload, request.app.state), index_name="orcid",
    )


@v2_router.post(
    "/indices/renkulab/search",
    response_model=IndexSearchResponse,
    response_model_exclude_none=True,
    tags=["Indices"],
)
async def renkulab_search_post(
    payload: IndexSearchRequest,
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexSearchResponse | JSONResponse:
    """Semantic search against the Renkulab index.

    ``target`` (optional) restricts the search to one of
    ``projects | datasets | users | groups | workflows``; omit to search
    across all configured entity types.
    """
    return await _search_response_or_unavailable(
        await run_renkulab_search(payload, request.app.state), index_name="renkulab",
    )


@v2_router.post(
    "/indices/swissubase/search",
    response_model=IndexSearchResponse,
    response_model_exclude_none=True,
    tags=["Indices"],
)
async def swissubase_search_post(
    payload: IndexSearchRequest,
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexSearchResponse | JSONResponse:
    """Semantic search against the SWISSUbase index."""
    return await _search_response_or_unavailable(
        await run_swissubase_search(payload, request.app.state),
        index_name="swissubase",
    )


@v2_router.post(
    "/indices/ethz_research_collection/search",
    response_model=IndexSearchResponse,
    response_model_exclude_none=True,
    tags=["Indices"],
)
async def ethz_research_collection_search_post(
    payload: IndexSearchRequest,
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexSearchResponse | JSONResponse:
    """Hybrid query against the ETH Research Collection index.

    ``target`` picks one of ``chunks`` (default), ``articles``, ``persons``,
    ``organizations``. ``filter_payload`` is forwarded as the ChromaDB-style
    ``where`` clause. Mode is fixed to ``hybrid``; for other modes use the
    standalone serve app directly.
    """
    return await _search_response_or_unavailable(
        await run_ethz_research_collection_search(payload, request.app.state),
        index_name="ethz_research_collection",
    )


@v2_router.post(
    "/indices/oamonitor/search",
    response_model=IndexSearchResponse,
    response_model_exclude_none=True,
    tags=["Indices"],
)
async def oamonitor_search_post(
    payload: IndexSearchRequest,
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexSearchResponse | JSONResponse:
    """Semantic search against the OAM-CH index.

    Use ``target`` to pick the entity collection: ``journals`` (default),
    ``publications``, ``publishers``, ``organisations``.
    """
    return await _search_response_or_unavailable(
        await run_oamonitor_search(payload, request.app.state),
        index_name="oamonitor",
    )


@v2_router.post(
    "/indices/dockerhub/search",
    response_model=IndexSearchResponse,
    response_model_exclude_none=True,
    tags=["Indices"],
)
async def dockerhub_search_post(
    payload: IndexSearchRequest,
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexSearchResponse | JSONResponse:
    """Semantic search against the Docker Hub index."""
    return await _search_response_or_unavailable(
        await run_dockerhub_search(payload, request.app.state),
        index_name="dockerhub",
    )


# --- /v2/indices/<name>/search for the CLI-managed catalogs ---------------
# These catalogs are populated by `python -m src.index.<name>` from cron
# (no v2 ingest route) but have populated Qdrant collections. The thin
# shims in `src.v2.indices.cli_catalogs` translate `IndexSearchRequest`
# to each catalog's existing query function.


@v2_router.post(
    "/indices/ror/search",
    response_model=IndexSearchResponse,
    response_model_exclude_none=True,
    tags=["Indices"],
)
async def ror_search_post(
    payload: IndexSearchRequest,
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexSearchResponse | JSONResponse:
    """Semantic search against the ROR organisation index (125k+ orgs)."""
    return await _search_response_or_unavailable(
        await run_ror_search(payload, request.app.state),
        index_name="ror",
    )


@v2_router.post(
    "/indices/infoscience/search",
    response_model=IndexSearchResponse,
    response_model_exclude_none=True,
    tags=["Indices"],
)
async def infoscience_search_post(
    payload: IndexSearchRequest,
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexSearchResponse | JSONResponse:
    """Semantic search against the Infoscience publications index.

    Use ``target`` to pick the collection: ``chunks`` (default),
    ``articles``, ``persons``, ``organizations``.
    """
    return await _search_response_or_unavailable(
        await run_infoscience_search(payload, request.app.state),
        index_name="infoscience",
    )


@v2_router.post(
    "/indices/snsf/search",
    response_model=IndexSearchResponse,
    response_model_exclude_none=True,
    tags=["Indices"],
)
async def snsf_search_post(
    payload: IndexSearchRequest,
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexSearchResponse | JSONResponse:
    """Semantic search against the SNSF grants index."""
    return await _search_response_or_unavailable(
        await run_snsf_search(payload, request.app.state),
        index_name="snsf",
    )


@v2_router.post(
    "/indices/epfl_graph/search",
    response_model=IndexSearchResponse,
    response_model_exclude_none=True,
    tags=["Indices"],
)
async def epfl_graph_search_post(
    payload: IndexSearchRequest,
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexSearchResponse | JSONResponse:
    """Semantic search against the EPFL Graph disciplines ontology."""
    return await _search_response_or_unavailable(
        await run_epfl_graph_search(payload, request.app.state),
        index_name="epfl_graph",
    )


@v2_router.post(
    "/indices/zenodo_communities/search",
    response_model=IndexSearchResponse,
    response_model_exclude_none=True,
    tags=["Indices"],
)
async def zenodo_communities_search_post(
    payload: IndexSearchRequest,
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexSearchResponse | JSONResponse:
    """Lexical (ILIKE) search against the institutional Zenodo communities
    registry.

    No semantic infrastructure — zenodo_communities is a tiny ~469-row
    DuckDB-only registry where substring scans across `title` /
    `description` / `keywords` finish in milliseconds. Title hits
    outrank description hits outrank keyword hits.
    """
    return await _search_response_or_unavailable(
        await run_communities_search(payload, request.app.state),
        index_name="zenodo_communities",
    )


# --- /v2/indices/freshness ------------------------------------------------
# Single roll-up over every supported catalog's `last_updated` for the Hub
# Overview / monitoring. One round-trip instead of 14 separate stats calls.


class _CatalogFreshness(BaseModel):
    provider: str
    count: int
    last_updated: datetime | None = None
    age_seconds: float | None = Field(
        default=None,
        description=(
            "Seconds since `last_updated`. `null` when the catalog is empty "
            "or has no timestamp-like column to read from."
        ),
    )


class _FreshnessResponse(BaseModel):
    as_of: datetime
    catalogs: list[_CatalogFreshness]
    oldest_provider: str | None = None
    oldest_age_seconds: float | None = None


@v2_router.get(
    "/indices/freshness",
    response_model=_FreshnessResponse,
    response_model_exclude_none=True,
    tags=["Indices"],
)
async def index_freshness_get(
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> _FreshnessResponse:
    """Aggregate `last_updated` across every supported catalog.

    Returns one row per provider plus the `oldest_provider` / `oldest_age_seconds`
    convenience fields that Open Pulse Hub can alert on directly. Providers
    whose resources are unavailable on this deployment surface with
    `count=0, last_updated=null` so the response shape is uniform.
    """
    now = datetime.now(timezone.utc)

    def _one(provider: str) -> _CatalogFreshness:
        try:
            store = fetch_store_for_stats(provider, request.app.state)
        except UnknownIndexProviderError:
            return _CatalogFreshness(provider=provider, count=0)
        if store is None:
            return _CatalogFreshness(provider=provider, count=0)
        try:
            stats = collect_index_stats(provider, store.connect())
        except Exception:  # noqa: BLE001 — keep the roll-up resilient
            logger.exception("index freshness: %s collect failed", provider)
            return _CatalogFreshness(provider=provider, count=0)
        if stats.last_updated is None:
            return _CatalogFreshness(provider=provider, count=stats.count)
        # Both sides timezone-aware (collect_index_stats normalises to UTC).
        age = (now - stats.last_updated).total_seconds()
        return _CatalogFreshness(
            provider=provider,
            count=stats.count,
            last_updated=stats.last_updated,
            age_seconds=age,
        )

    catalogs = await asyncio.to_thread(
        lambda: [_one(p) for p in INDEX_STATS_SUPPORTED_PROVIDERS],
    )
    aged = [c for c in catalogs if c.age_seconds is not None]
    oldest = max(aged, key=lambda c: c.age_seconds) if aged else None
    return _FreshnessResponse(
        as_of=now,
        catalogs=catalogs,
        oldest_provider=oldest.provider if oldest else None,
        oldest_age_seconds=oldest.age_seconds if oldest else None,
    )


# --- /v2/indices/<name>/compact -------------------------------------------
# Operator endpoint: EXPORT/IMPORT round-trip the per-provider DuckDB
# file to reclaim space tombstoned by upsert churn. Mirrors what
# `just compact-indexes` does offline, but online and per-provider.


@v2_router.post(
    "/indices/{provider}/compact",
    response_model=CompactResult,
    response_model_exclude_none=True,
    tags=["Indices"],
)
async def index_compact_post(
    provider: Annotated[str, Path(description="One of the supported index providers.")],
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> CompactResult | JSONResponse:
    """Run an EXPORT/IMPORT round-trip on `provider`'s DuckDB.

    Closes the in-process Store cached on `app.state` so the file lock
    is released, then opens a fresh connection, exports every table to
    a sibling tempdir, swaps the DuckDB atomically with a `.bak`
    fallback, and reports `bytes_before` / `bytes_after`. The next
    stats / search call lazily re-opens.

    This is a maintenance operation — call when the server is quiet.
    `openalex` (3.7 GB) can take a couple of minutes.
    """
    try:
        store = fetch_store_for_stats(provider, request.app.state)
    except UnknownIndexProviderError as exc:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc)},
        )
    if store is None:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": f"{provider} index resources unavailable on this deployment",
            },
        )
    db_path = getattr(store, "db_path", None)
    if db_path is None:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": f"{provider} Store does not expose `db_path`; compact unsupported",
            },
        )
    # Close any in-process write handle so EXPORT can re-open the file.
    close_cached_resources_for(provider, request.app.state)
    try:
        result = await asyncio.to_thread(compact_duckdb, provider, db_path)
    except FileNotFoundError as exc:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc)},
        )
    except Exception as exc:  # noqa: BLE001 — surface as 500 with the message
        logger.exception("index compact failed: provider=%s", provider)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": f"compact failed: {exc}"},
        )
    return result


@v2_router.get(
    "/indices/{provider}/stats",
    response_model=IndexStatsResponse,
    response_model_exclude_none=True,
    tags=["Indices"],
)
async def index_stats_get(
    provider: Annotated[str, Path(description="One of the supported index providers.")],
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexStatsResponse | JSONResponse:
    """Read-only catalog stats: total rows + per-table breakdown + last_updated.

    External consumers (Open Pulse Hub Overview, dashboards) used to poll
    each `.duckdb` file with `duckdb.connect(path, read_only=True)`. That
    fails as soon as the GME holds a write connection (auto-ingest et al)
    because DuckDB's advisory lock is per-process. This endpoint runs the
    same `SELECT COUNT(*)` queries on the GME's already-open connection,
    so the file lock is never contested.
    """

    try:
        store = fetch_store_for_stats(provider, request.app.state)
    except UnknownIndexProviderError as exc:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc)},
        )
    if store is None:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": f"{provider} index resources unavailable on this deployment",
            },
        )

    try:
        stats = await asyncio.to_thread(
            collect_index_stats, provider, store.connect(),
        )
    except Exception as exc:  # noqa: BLE001 — surface as 503 + log
        logger.exception("index stats query failed: provider=%s", provider)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": f"stats query failed: {exc}"},
        )
    return stats


@v2_router.get(
    "/indices/jobs/{job_id}",
    response_model=IndexIngestJob,
    response_model_exclude_none=True,
    tags=["Indices"],
)
async def index_ingest_job_status(
    job_id: Annotated[str, Path(description="Job id returned by an index ingest POST.")],
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
) -> IndexIngestJob | JSONResponse:
    """Retrieve the status (and summary, when complete) of an index-ingest job."""

    job_store = _resolve_index_ingest_job_store(request)
    if job_store is None:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": "index ingest job store unavailable: provider cache is disabled",
            },
        )

    record = job_store.get(job_id)
    if record is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": f"no index ingest job found with id '{job_id}'"},
        )
    return record


# --- /v2/indices/<provider>/reset ----------------------------------------
# Cold-start a single provider's index: wipe DuckDB + Qdrant collection(s).
# The opt-in `wipe_cache=true` query param also clears the per-provider
# ProviderCache so re-ingest re-fetches from upstream instead of replaying
# cached responses. Token-gated; intentionally not idempotent-on-DELETE
# at the HTTP level (200 + structured result), since clients usually want
# to know what was actually reclaimed.


@v2_router.delete(
    "/indices/{provider}/reset",
    tags=["Indices"],
    response_model=None,
)
async def reset_provider_index(
    provider: str,
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
    wipe_qdrant: bool = True,
    wipe_cache: bool = False,
) -> dict[str, Any] | JSONResponse:
    """Wipe one provider's DuckDB + Qdrant collection(s), enabling a
    cold-start re-ingest.

    Query flags:
      - ``wipe_qdrant=true|false`` (default true): drop Qdrant
        collections too. Set false for a DuckDB-only reset.
      - ``wipe_cache=true|false`` (default false): also clear the
        per-provider ProviderCache. Use when upstream data has
        shifted; default keeps cached upstream responses so
        re-ingest is fast.
    """
    from src.v2.indices.reset import (  # noqa: PLC0415
        UnknownProviderError,
        reset_index,
    )

    try:
        result = await asyncio.to_thread(
            reset_index,
            provider,
            app_state=request.app.state,
            wipe_qdrant=wipe_qdrant,
            wipe_cache=wipe_cache,
        )
    except UnknownProviderError as exc:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc)},
        )
    return {
        "provider": result.provider,
        "duckdb_deleted": result.duckdb_deleted,
        "duckdb_bytes_reclaimed": result.duckdb_bytes_reclaimed,
        "qdrant_collections_attempted": list(result.qdrant_collections_attempted),
        "qdrant_collections_dropped": list(result.qdrant_collections_dropped),
        "qdrant_skipped": result.qdrant_skipped,
        "cache_cleared": result.cache_cleared,
        "elapsed_seconds": result.elapsed_seconds,
    }


@v2_router.delete(
    "/indices/reset-all",
    tags=["Indices"],
)
async def reset_all_indices(
    request: Request,
    _token: Annotated[str, Depends(verify_token)],
    wipe_qdrant: bool = True,
    wipe_cache: bool = False,
) -> dict[str, Any]:
    """Wipe every known provider in one call.

    Failures on individual providers don't stop the rest; each provider
    returns its own result entry. Use carefully — this is an operator
    tool for full re-ingest, not a routine cache flush.
    """
    from src.v2.indices.reset import reset_all  # noqa: PLC0415

    results = await asyncio.to_thread(
        reset_all,
        app_state=request.app.state,
        wipe_qdrant=wipe_qdrant,
        wipe_cache=wipe_cache,
    )
    return {
        "count": len(results),
        "results": [
            {
                "provider": r.provider,
                "duckdb_deleted": r.duckdb_deleted,
                "duckdb_bytes_reclaimed": r.duckdb_bytes_reclaimed,
                "qdrant_collections_dropped": list(r.qdrant_collections_dropped),
                "qdrant_skipped": r.qdrant_skipped,
                "cache_cleared": r.cache_cleared,
                "elapsed_seconds": r.elapsed_seconds,
            }
            for r in results
        ],
    }


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
        except Exception:  # noqa: BLE001 — probe must never crash health
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
