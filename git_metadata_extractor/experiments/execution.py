"""Local adapter to the same extraction engine used by the HTTP API."""

from __future__ import annotations

import asyncio
import json
import os
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from typing import TYPE_CHECKING

from pydantic import Field, JsonValue

from git_metadata_extractor.agents.llm.model_config import (
    ENV_VAR_MAPPINGS,
    load_model_config,
)
from git_metadata_extractor.agents.llm.runtime import (
    V2LLMRuntime,
    reset_request_model_override,
    set_request_model_override,
)
from git_metadata_extractor.agents.tool_catalog import tool_scope
from git_metadata_extractor.api_models import V2ExtractResponse
from git_metadata_extractor.experiments.builtins import default_catalog
from git_metadata_extractor.experiments.catalog import ComponentCatalog, selected_model
from git_metadata_extractor.mapping import mapping_manifest
from git_metadata_extractor.models import InternalModel
from git_metadata_extractor.observation.evidence import _safe
from git_metadata_extractor.observation.query_log import query_log_var
from git_metadata_extractor.providers.base import ProviderNotFoundError
from git_metadata_extractor.providers.cache import ProviderCache
from git_metadata_extractor.providers.detection.github_url_classifier import (
    classify_github_url,
)
from git_metadata_extractor.providers.detection.models import GitHubURLType
from git_metadata_extractor.providers.runtime import (
    build_provider_resources,
    close_shared_providers,
)
from git_metadata_extractor.services.extraction import (
    ExtractionOptions,
    execute_extraction,
)

if TYPE_CHECKING:
    from git_metadata_extractor.agents.models import ProviderSet
    from git_metadata_extractor.experiments.models import PipelineProfile

SETTINGS = (
    "SOURCES_API_URL",
    "V2_LINK_VERACITY_ENABLED",
    "V2_CONTEXT_SUMMARY_SCOUT_MODE",
    "V2_PROVIDER_CACHE_ENABLED",
    "V2_PIPELINE_CACHE_ENABLED",
    "V2_APPLY_CRITIC_PRUNING",
    "V2_CONCEPT_TAGGING_ENABLED",
    "V2_HYBRID_REFINER_ENABLED",
    "V2_CANONICAL_OUTPUT_ENABLED",
    "V2_SUBSTRATE_ENABLED",
    "V2_EXPAND_OWNED_REPOS",
    "V2_MAX_REPO_FANOUT_ORG",
    "V2_MAX_REPO_FANOUT_USER",
    "V2_LLM_REQUEST_LIMIT",
    "V2_LLM_TOOL_CALLS_LIMIT",
    "V2_LLM_TIMEOUT_SECONDS",
)


@lru_cache(maxsize=1)
def code_fingerprint() -> str:
    """Includes uncommitted package code, prompts and schemas; excludes credentials."""
    root = Path(__file__).resolve().parents[1]
    digest = sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix in {".py", ".json", ".jsonld", ".md", ".ttl", ".yaml"}:
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def execution_manifest(catalog, profile, provider_mode):
    manifest = catalog.manifest(profile)
    public_keys = {
        "model",
        "provider",
        "base_url",
        "api_key_env",
        "temperature",
        "max_tokens",
        "max_completion_tokens",
        "max_retries",
        "timeout",
        "allow_tools",
    }
    model_configs = {
        name: [
            {key: _safe(value) for key, value in config.items() if key in public_keys}
            for config in load_model_config(name)
            if isinstance(config, dict)
        ]
        for name in ENV_VAR_MAPPINGS
    }
    manifest.update(
        mappings=mapping_manifest(),
        code_fingerprint=code_fingerprint(),
        provider_mode=provider_mode,
        settings={key: os.getenv(key) for key in SETTINGS},
        model_configs=model_configs,
    )
    manifest["fingerprint"] = sha256(
        json.dumps(manifest, sort_keys=True).encode(),
    ).hexdigest()
    return manifest


class ProfileExecution(InternalModel):
    response: V2ExtractResponse
    manifest: dict[str, JsonValue]
    elapsed_seconds: float = Field(ge=0)
    pipeline_cache_hit: bool


async def run_profile(  # noqa: PLR0913 - explicit local execution dependencies
    profile: PipelineProfile,
    source_url: str,
    *,
    cache_dir: Path,
    catalog: ComponentCatalog | None = None,
    provider_mode: str = "mock",
    providers: ProviderSet | None = None,
) -> ProfileExecution:
    """Run locally with isolated state, no ingestion/store writes and no env mutation."""
    token = set_request_model_override(None)
    try:
        return await _run_profile(
            profile,
            source_url,
            cache_dir=cache_dir,
            catalog=catalog,
            provider_mode=provider_mode,
            providers=providers,
        )
    finally:
        reset_request_model_override(token)


async def _run_profile(  # noqa: C901, PLR0913 - scoped extraction lifecycle
    profile,
    source_url,
    *,
    cache_dir,
    catalog,
    provider_mode,
    providers,
):
    if provider_mode not in {"mock", "live"}:
        msg = "provider_mode must be mock or live"
        raise ValueError(msg)
    classification = classify_github_url(source_url, probe_accounts=False)
    catalog = catalog or default_catalog()
    catalog.validate(profile)
    # Resolve model selections before constructing live providers or starting work.
    with selected_model(profile.model):
        if profile.runtime != "rule_based":
            V2LLMRuntime()._resolve_model_config()  # noqa: SLF001 - same preflight as production runtime
        for reference in profile.agents.values():
            if reference.model:
                with selected_model(reference.model):
                    V2LLMRuntime()._resolve_model_config()  # noqa: SLF001
    if provider_mode == "live" and providers is None:
        if not os.getenv("GME_GITHUB_TOKEN"):
            msg = "Missing required environment variable: GME_GITHUB_TOKEN"
            raise ValueError(msg)
        if os.getenv("V2_USE_MOCK_PROVIDERS", "true").lower() not in {
            "false",
            "0",
            "no",
            "off",
            "f",
            "n",
        }:
            msg = "Live trials require V2_USE_MOCK_PROVIDERS=false"
            raise ValueError(msg)
    manifest = execution_manifest(
        catalog,
        profile,
        "injected" if providers else provider_mode,
    )
    cache = ProviderCache(Path(cache_dir) / manifest["fingerprint"] / "providers.db")
    state = SimpleNamespace(
        v2_provider_cache=cache,
        v2_execution_profile=manifest,
        v2_orchestrator=catalog.build(profile, cache=cache),
    )
    if providers is not None:
        state.v2_provider_set = providers
    resources = build_provider_resources(
        state,
        use_mock=provider_mode == "mock",
        repository_scope=classification.detected_type == GitHubURLType.REPOSITORY,
        include_rag=provider_mode == "live",
    )
    bundle = resources.bundle
    previous_log = query_log_var.set(None)
    started = perf_counter()
    try:
        if classification.detected_type == GitHubURLType.USER:
            try:
                account = await asyncio.to_thread(
                    bundle.github.get_user,
                    classification.owner,
                )
            except ProviderNotFoundError:
                account = await asyncio.to_thread(
                    bundle.github.get_organization,
                    classification.owner,
                )
            if account.get("type") == "Organization":
                classification = classification.model_copy(
                    update={"detected_type": GitHubURLType.ORGANIZATION},
                )
        with selected_model(profile.model), tool_scope(catalog.tools, profile.toolsets):
            result = await execute_extraction(
                ExtractionOptions(
                    source_url=source_url,
                    agent_runtime=profile.runtime,
                    include_context_summary=True,
                    read_only=True,
                ),
                providers=bundle,
                orchestrator=state.v2_orchestrator,
                cache=cache,
                execution_profile=manifest,
                classification=classification,
            )
            response = result.response
        elapsed = perf_counter() - started
        return ProfileExecution(
            response=response,
            manifest=manifest,
            elapsed_seconds=elapsed,
            pipeline_cache_hit=result.cache_hit,
        )
    finally:
        query_log_var.reset(previous_log)
        await resources.aclose()
        await close_shared_providers(state)
