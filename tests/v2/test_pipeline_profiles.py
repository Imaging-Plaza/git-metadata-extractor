# Test-local imports and callback signatures keep fixtures isolated.
# ruff: noqa: PLC0415, ARG001, ARG005
from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from git_metadata_extractor.agents.models import AgentResult, ProviderSet
from git_metadata_extractor.agents.registry import AgentRuntimeRegistry
from git_metadata_extractor.agents.runtime import AgentRuntime
from git_metadata_extractor.pipeline.stages.models import ContextBundle
from git_metadata_extractor.providers.mock_github import MockGitHubProvider


def test_registry_can_replace_llm_implementations_without_dispatch_edits():
    async def alternative(context, providers):
        return AgentResult(data={"id": "https://github.com/acme/tool"})

    registry = AgentRuntimeRegistry()
    registry.register_runner("repo_agent", alternative, runtime=AgentRuntime.LLM)
    assert (
        registry.resolve_runner(
            stage_key="repo_agent", runtime=AgentRuntime.LLM, detected_type="repository",
        )
        is alternative
    )
    assert (
        registry.resolve_runner(
            stage_key="repo_agent", runtime=AgentRuntime.LLM, detected_type="user",
        )
        is alternative
    )


def test_profile_rejects_unknown_fields_and_invalid_concurrency():
    from git_metadata_extractor.experiments.models import PipelineProfile

    with pytest.raises(ValidationError):
        PipelineProfile(name="broken", max_concurrent_agents=0)
    with pytest.raises(ValidationError):
        PipelineProfile(name="broken", agent_runtme="llm")


def test_profile_checks_stage_order_and_requires_root():
    from git_metadata_extractor.experiments.models import PipelineProfile

    with pytest.raises(ValidationError):
        PipelineProfile(
            name="broken", stages={"repository": ["context_gather", "person_agents"]},
        )
    with pytest.raises(ValidationError):
        PipelineProfile(
            name="broken",
            stages={
                "repository": [
                    "context_gather",
                    "repo_agent",
                    "contribution_agents",
                    "person_agents",
                ],
            },
        )


def test_named_collectors_compose_and_custom_agent_is_used():
    from git_metadata_extractor.experiments.builtins import default_catalog
    from git_metadata_extractor.experiments.models import PipelineProfile
    from git_metadata_extractor.providers.detection.github_url_classifier import (
        classify_github_url,
    )

    catalog = default_catalog()
    seen = []

    async def first(context, providers, options):
        seen.append("first")
        return ContextBundle(
            detected_type="repository",
            context={"repository": {"metadata": {"name": options["name"]}}},
        )

    async def second(context, providers, options):
        seen.append(context.context["repository"]["metadata"]["name"])
        return ContextBundle(
            detected_type="repository",
            context={"repository": {"readme_content": "A README"}},
        )

    def agent_factory(options, cache):
        async def run(context, providers):
            assert (
                context["gathered_context"]["repository"]["readme_content"]
                == "A README"
            )
            return AgentResult(
                data={
                    "id": "https://github.com/acme/tool",
                    "type": "schema:SoftwareSourceCode",
                    "schema:name": options["name"],
                },
            )

        return run

    catalog.register_collector("test.first", first, version="1")
    catalog.register_collector("test.second", second, version="1")
    catalog.register_agent(
        "test.repo", agent_factory, version="1", runtime="rule_based",
    )
    profile = PipelineProfile(
        name="custom",
        collectors=[
            {"component": "test.first", "options": {"name": "collected"}},
            {"component": "test.second"},
        ],
        agents={
            "repo_agent": {
                "component": "test.repo",
                "options": {"name": "custom result"},
            },
        },
        stages={"repository": ["context_gather", "repo_agent"]},
    )
    orchestrator = catalog.build(profile)
    result = asyncio.run(
        orchestrator.execute(
            orchestrator.get_execution_plan("repository"),
            ProviderSet(github=MockGitHubProvider()),
            {
                "url_info": classify_github_url("https://github.com/acme/tool"),
                "source_url": "https://github.com/acme/tool",
                "agent_runtime": "rule_based",
            },
        ),
    )
    assert seen == ["first", "collected"]
    assert result.agent_results["repo_agent"].data["schema:name"] == "custom result"


def test_manifest_changes_when_component_version_or_model_changes():
    from git_metadata_extractor.experiments.builtins import default_catalog
    from git_metadata_extractor.experiments.models import PipelineProfile

    catalog = default_catalog()
    profile = PipelineProfile(name="baseline", runtime="llm")
    first = catalog.manifest(profile)
    assert catalog.manifest(profile)["fingerprint"] == first["fingerprint"]
    other = PipelineProfile.model_validate(
        {**profile.model_dump(), "model": {"model": "another-model"}},
    )
    assert catalog.manifest(other)["fingerprint"] != first["fingerprint"]


def test_unknown_components_fail_before_any_collection():
    from git_metadata_extractor.experiments.builtins import default_catalog
    from git_metadata_extractor.experiments.models import PipelineProfile

    with pytest.raises(ValueError, match="unknown collector"):
        default_catalog().build(
            PipelineProfile(name="bad", collectors=[{"component": "not.registered"}]),
        )


def test_rule_based_profile_cannot_select_an_llm_agent():
    from git_metadata_extractor.experiments.builtins import default_catalog
    from git_metadata_extractor.experiments.models import PipelineProfile

    with pytest.raises(ValueError, match="runtime"):
        default_catalog().build(
            PipelineProfile(
                name="bad", agents={"repo_agent": {"component": "llm.repository"}},
            ),
        )


def test_nested_model_selections_inherit_provider_and_restore(monkeypatch):
    from git_metadata_extractor.agents.llm.runtime import get_request_model_override
    from git_metadata_extractor.experiments.catalog import selected_model
    from git_metadata_extractor.experiments.models import ModelSelection

    monkeypatch.setenv("V2_ALLOW_REQUEST_MODEL_OVERRIDE", "true")
    with selected_model(
        ModelSelection(
            model="outer", provider="openai-compatible", api_key_env="RCP_TOKEN",
        ),
    ):
        with selected_model(ModelSelection(model="inner")):
            assert get_request_model_override()["model"] == "inner"
            assert get_request_model_override()["api_key_env"] == "RCP_TOKEN"
        assert get_request_model_override()["model"] == "outer"
    assert get_request_model_override() is None


def test_profile_drives_real_extraction_and_separates_caches(tmp_path, monkeypatch):
    from git_metadata_extractor.experiments.execution import run_profile
    from git_metadata_extractor.experiments.models import PipelineProfile

    monkeypatch.setenv("V2_PIPELINE_CACHE_ENABLED", "true")
    profile = PipelineProfile(name="baseline")

    async def run():
        first = await run_profile(
            profile,
            "https://github.com/octocat/Hello-World",
            cache_dir=tmp_path,
            provider_mode="mock",
        )
        warm = await run_profile(
            profile,
            "https://github.com/octocat/Hello-World",
            cache_dir=tmp_path,
            provider_mode="mock",
        )
        other = await run_profile(
            PipelineProfile(name="other", max_concurrent_agents=2),
            "https://github.com/octocat/Hello-World",
            cache_dir=tmp_path,
            provider_mode="mock",
        )
        return first, warm, other

    first, warm, other = asyncio.run(run())
    assert first.response.output.graph
    assert first.response.execution_profile["profile"]["name"] == "baseline"
    assert warm.pipeline_cache_hit is True
    assert other.pipeline_cache_hit is False
    assert first.manifest["fingerprint"] != other.manifest["fingerprint"]


def test_model_overrides_fail_instead_of_silently_benchmarking_default(monkeypatch):
    from git_metadata_extractor.experiments.catalog import selected_model
    from git_metadata_extractor.experiments.models import ModelSelection

    monkeypatch.delenv("V2_ALLOW_REQUEST_MODEL_OVERRIDE", raising=False)
    with pytest.raises(ValueError, match="V2_ALLOW_REQUEST_MODEL_OVERRIDE"), selected_model(ModelSelection(model="alternative")):
        pass


def test_experiment_runs_do_not_auto_ingest_or_write_to_store(tmp_path, monkeypatch):
    from git_metadata_extractor.api import auto_ingest
    from git_metadata_extractor.experiments.execution import run_profile
    from git_metadata_extractor.experiments.models import PipelineProfile
    from git_metadata_extractor.store import oxigraph

    def forbidden(*args, **kwargs):
        msg = "Benchmark attempted an external write"
        raise AssertionError(msg)

    for name in (
        "_maybe_schedule_github_repos_auto_ingest",
        "_maybe_schedule_github_users_auto_ingest",
        "_maybe_schedule_github_orgs_auto_ingest",
    ):
        monkeypatch.setattr(auto_ingest, name, forbidden)
    monkeypatch.setattr(oxigraph, "store_from_config", forbidden)
    monkeypatch.setenv("V2_SUBSTRATE_ENABLED", "true")
    result = asyncio.run(
        run_profile(
            PipelineProfile(name="read-only"),
            "https://github.com/octocat/Hello-World",
            cache_dir=tmp_path,
        ),
    )
    assert result.response.substrate


def test_component_option_schema_is_checked_before_execution():
    from pydantic import BaseModel, ConfigDict, Field

    from git_metadata_extractor.experiments.builtins import default_catalog
    from git_metadata_extractor.experiments.models import PipelineProfile

    class Options(BaseModel):
        model_config = ConfigDict(extra="forbid")
        limit: int = Field(ge=1)

    catalog = default_catalog()
    catalog.register_collector(
        "custom", lambda *args: None, version="2", options_model=Options,
    )
    with pytest.raises(ValidationError):
        catalog.validate(
            PipelineProfile(
                name="invalid",
                collectors=[{"component": "custom", "options": {"limit": 0}}],
            ),
        )


def test_component_versions_participate_in_fingerprint():
    from git_metadata_extractor.experiments.builtins import default_catalog
    from git_metadata_extractor.experiments.models import PipelineProfile

    first, second = default_catalog(), default_catalog()
    first.register_collector("custom", lambda *args: None, version="1")
    second.register_collector("custom", lambda *args: None, version="2")
    profile = PipelineProfile(name="custom", collectors=[{"component": "custom"}])
    assert (
        first.manifest(profile)["fingerprint"]
        != second.manifest(profile)["fingerprint"]
    )


def test_effective_environment_model_changes_execution_fingerprint(monkeypatch):
    import json

    from git_metadata_extractor.experiments.builtins import default_catalog
    from git_metadata_extractor.experiments.execution import execution_manifest
    from git_metadata_extractor.experiments.models import PipelineProfile

    catalog, profile = default_catalog(), PipelineProfile(name="models", runtime="llm")
    monkeypatch.setenv("V2_LLM_MODEL_OVERRIDE", "model-a")
    first = execution_manifest(catalog, profile, "mock")
    monkeypatch.setenv("V2_LLM_MODEL_OVERRIDE", "model-b")
    second = execution_manifest(catalog, profile, "mock")
    assert first["fingerprint"] != second["fingerprint"]
    monkeypatch.setenv(
        "LLM_ANALYSIS_MODELS",
        json.dumps(
            [{"model": "model-c", "provider": "openai", "api_key": "never-export-this"}],
        ),
    )
    third = execution_manifest(catalog, profile, "mock")
    assert third["fingerprint"] != second["fingerprint"]
    assert "never-export-this" not in json.dumps(third)
    monkeypatch.setenv("LLM_ANALYSIS_MODELS", json.dumps([{"model": "model-c", "max_tokens": 64, "api_key_env": "RCP_TOKEN"}]))
    fourth = execution_manifest(catalog, profile, "mock")
    monkeypatch.setenv("LLM_ANALYSIS_MODELS", json.dumps([{"model": "model-c", "max_tokens": 128, "api_key_env": "RCP_TOKEN"}]))
    fifth = execution_manifest(catalog, profile, "mock")
    assert fourth["fingerprint"] != fifth["fingerprint"]
    assert fifth["model_configs"]["run_llm_analysis"][0]["api_key_env"] == "RCP_TOKEN"


def test_mock_account_classification_never_uses_live_probe(tmp_path, monkeypatch):
    from git_metadata_extractor.experiments.benchmark import BenchmarkCase
    from git_metadata_extractor.experiments.execution import run_profile
    from git_metadata_extractor.experiments.models import PipelineProfile
    from git_metadata_extractor.providers.detection import github_url_classifier

    def forbidden(account):
        msg = "A mock benchmark attempted a live account probe"
        raise AssertionError(msg)

    monkeypatch.setattr(github_url_classifier, "_probe_account_type", forbidden)
    case = BenchmarkCase(id="account", source_url="https://github.com/octocat")
    result = asyncio.run(
        run_profile(
            PipelineProfile(name="account"), case.source_url, cache_dir=tmp_path,
        ),
    )
    assert result.response.detected_type == "user"


def test_owned_async_providers_are_closed(tmp_path, monkeypatch):
    from git_metadata_extractor.experiments import execution
    from git_metadata_extractor.experiments.models import PipelineProfile

    closed = []

    class Rag:
        async def aclose(self):
            closed.append("rag")

    bundle = ProviderSet(github=MockGitHubProvider(), infoscience_rag=Rag())

    def provide(state, **kwargs):
        from git_metadata_extractor.providers.runtime import ProviderResources
        return ProviderResources(bundle=bundle, owned=[bundle.infoscience_rag])

    monkeypatch.setattr(execution, "build_provider_resources", provide)
    asyncio.run(
        execution.run_profile(
            PipelineProfile(name="close"),
            "https://github.com/octocat/Hello-World",
            cache_dir=tmp_path,
        ),
    )
    assert closed == ["rag"]
