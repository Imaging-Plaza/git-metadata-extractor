"""Shared tools must compose predictably and remain isolated across trials."""

# ruff: noqa: PLC0415, ARG001, ARG005, PLR2004
from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_ai import Tool


def test_catalog_options_requirements_bundles_and_collision_detection():
    from git_metadata_extractor.agents.models import ProviderSet
    from git_metadata_extractor.agents.tool_catalog import (
        ToolCatalog,
        ToolContext,
        ToolRef,
        ToolSelection,
    )

    class Options(BaseModel):
        model_config = ConfigDict(extra="forbid")
        limit: int = Field(default=3, ge=1, le=10)

    made = []

    def factory(context, options):
        made.append(options)

        def lookup(query: str) -> dict:
            """Look up a record."""
            return {"query": query, "limit": options["limit"]}

        return Tool(lookup)

    catalog = ToolCatalog()
    catalog.register(
        "lookup", factory, version="1", requires=("ror",), options_model=Options,
    )
    catalog.register_bundle("research", ["lookup"])
    catalog.set_defaults("person_agent", ["research"])
    context = ToolContext(providers=ProviderSet(github=None))
    assert catalog.build("person_agent", context) == []
    assert made == []
    with pytest.raises(ValueError, match="ror"):
        catalog.build(
            "person_agent", context, ToolSelection(tools=[ToolRef(component="lookup")]),
        )
    with pytest.raises(ValidationError):
        catalog.validate_selection(
            ToolSelection(tools=[ToolRef(component="lookup", options={"limit": 0})]),
        )
    context = ToolContext(providers=ProviderSet(github=None, ror=object()))
    selected = ToolSelection(tools=[ToolRef(component="lookup", options={"limit": 7})])
    built = catalog.build("person_agent", context, selected)
    assert len(built) == 1
    assert built[0].function("hello") == {"query": "hello", "limit": 7}
    catalog.register("other", factory, version="1", options_model=Options)
    with pytest.raises(ValueError, match="Duplicate tool name"):
        catalog.build(
            "person_agent", context, ToolSelection(tools=[ToolRef(component="other")]),
        )


def test_task_local_toolsets_restore_and_do_not_leak():
    from git_metadata_extractor.agents.models import ProviderSet
    from git_metadata_extractor.agents.tool_catalog import (
        ToolCatalog,
        ToolContext,
        ToolRef,
        ToolSelection,
        resolve_tools,
        tool_scope,
    )

    catalog = ToolCatalog()

    def factory(context, options):
        def ping() -> str:
            """Return a local marker."""
            return "pong"

        return Tool(ping)

    catalog.register("ping", factory, version="1")
    ctx = ToolContext(providers=ProviderSet(github=None))

    async def trial(enabled):
        selection = ToolSelection(
            include_defaults=False, tools=[ToolRef(component="ping")] if enabled else [],
        )
        with tool_scope(catalog, {"repo_agent": selection}):
            await asyncio.sleep(0)
            return [t.name for t in resolve_tools("repo_agent", ctx)]

    async def both():
        return await asyncio.gather(trial(enabled=True), trial(enabled=False))

    assert asyncio.run(both()) == [["ping"], []]
    with tool_scope(catalog, {"repo_agent": ToolSelection(include_defaults=False)}):
        with pytest.raises(RuntimeError), tool_scope(
            catalog, {"repo_agent": ToolSelection(tools=[ToolRef(component="ping")])},
        ):
            raise RuntimeError
        assert resolve_tools("repo_agent", ctx) == []


def test_default_repository_and_membership_tool_names_are_preserved():
    from git_metadata_extractor.agents.llm.tool_defaults import default_tool_catalog
    from git_metadata_extractor.agents.models import ProviderSet
    from git_metadata_extractor.agents.tool_catalog import ToolContext
    from git_metadata_extractor.providers.mock_github import MockGitHubProvider
    from git_metadata_extractor.providers.mock_orcid import MockORCIDProvider

    catalog = default_tool_catalog()
    context = ToolContext(
        providers=ProviderSet(github=MockGitHubProvider(), orcid=MockORCIDProvider()),
    )
    assert [t.name for t in catalog.build("repo_agent", context)] == [
        "list_disciplines",
        "fetch_link_content_via_selenium",
        "query_dependencies",
    ]
    assert [t.name for t in catalog.build("membership_agent", context)] == [
        "fetch_link_content_via_selenium",
        "get_orcid_record",
        "query_orcid",
    ]


def test_tool_configuration_affects_profile_fingerprint_and_rejects_unused_roles():
    from git_metadata_extractor.experiments.builtins import default_catalog
    from git_metadata_extractor.experiments.models import PipelineProfile

    catalog = default_catalog()
    baseline = PipelineProfile(name="trial", runtime="llm")
    altered = PipelineProfile(
        name="trial",
        runtime="llm",
        toolsets={"article_agent": {"include_defaults": False, "tools": []}},
    )
    assert (
        catalog.manifest(baseline)["fingerprint"]
        != catalog.manifest(altered)["fingerprint"]
    )
    with pytest.raises(ValueError, match="llm runtime"):
        catalog.validate(
            PipelineProfile(name="invalid", toolsets={"article_agent": {"tools": []}}),
        )
    with pytest.raises(ValueError, match="Unknown tool component"):
        catalog.validate(
            PipelineProfile(
                name="unknown",
                runtime="llm",
                toolsets={"article_agent": {"tools": [{"component": "missing"}]}},
            ),
        )


def test_generic_repository_tool_is_bounded_and_lazy():
    from git_metadata_extractor.agents.llm.tool_defaults import default_tool_catalog
    from git_metadata_extractor.agents.models import ProviderSet
    from git_metadata_extractor.agents.tool_catalog import (
        ToolContext,
        ToolRef,
        ToolSelection,
    )

    calls = []

    class Github:
        def get_repository_readme(self, name):
            calls.append(name)
            return "a" * 2000

    catalog = default_tool_catalog()
    context = ToolContext(providers=ProviderSet(github=Github()))
    tool = catalog.build(
        "article_agent",
        context,
        ToolSelection(
            include_defaults=False,
            tools=[ToolRef(component="github.repository", options={"max_chars": 1000})],
        ),
    )[0]
    assert not calls
    result = asyncio.run(tool.function("owner/repo", section="readme"))
    assert calls == ["owner/repo"]
    assert result["truncated"] is True
    assert len(result["content"]) == 1000
    assert result["source_url"] == "https://github.com/owner/repo"


def test_real_agent_receives_selected_tools_without_default_construction():
    from git_metadata_extractor.agents.llm.repository import LLMRepositoryAgentV2
    from git_metadata_extractor.agents.llm.runtime import LLMRuntimeError
    from git_metadata_extractor.agents.llm.tool_defaults import default_tool_catalog
    from git_metadata_extractor.agents.models import ProviderSet
    from git_metadata_extractor.agents.tool_catalog import (
        ToolRef,
        ToolSelection,
        tool_scope,
    )
    from git_metadata_extractor.providers.mock_github import MockGitHubProvider

    captured = []

    class Runtime:
        async def run_json_prompt(self, **kwargs):
            captured.extend(kwargs["tools"])
            msg = "stopped before model call"
            raise LLMRuntimeError(msg)

    catalog = default_tool_catalog()
    with tool_scope(
        catalog,
        {
            "repo_agent": ToolSelection(
                include_defaults=False, tools=[ToolRef(component="github.repository")],
            ),
        },
    ), pytest.raises(LLMRuntimeError, match="stopped before model call"):
        asyncio.run(
            LLMRepositoryAgentV2(llm_runtime=Runtime()).run(
                {"full_name": "octocat/Hello-World"},
                ProviderSet(github=MockGitHubProvider()),
            ),
        )
    assert [t.name for t in captured] == ["fetch_github_repository"]


def test_tool_version_changes_profile_fingerprint():
    from git_metadata_extractor.experiments.builtins import default_catalog
    from git_metadata_extractor.experiments.models import PipelineProfile

    first, second = default_catalog(), default_catalog()
    first.tools.register("custom", lambda ctx, opts: [], version="1")
    second.tools.register("custom", lambda ctx, opts: [], version="2")
    profile = PipelineProfile(name="version", runtime="llm")
    assert (
        first.manifest(profile)["fingerprint"]
        != second.manifest(profile)["fingerprint"]
    )


def test_identity_tool_requires_at_least_one_provider_when_explicit():
    from git_metadata_extractor.agents.llm.tool_defaults import default_tool_catalog
    from git_metadata_extractor.agents.models import ProviderSet
    from git_metadata_extractor.agents.tool_catalog import (
        ToolContext,
        ToolRef,
        ToolSelection,
    )

    with pytest.raises(ValueError, match="ror"):
        default_tool_catalog().build(
            "org_agent",
            ToolContext(providers=ProviderSet(github=None)),
            ToolSelection(
                include_defaults=False,
                tools=[ToolRef(component="organization.identity")],
            ),
        )


def test_all_provider_defaults_match_pre_refactor_toolsets():
    import json
    from pathlib import Path

    from git_metadata_extractor.agents.llm.tool_defaults import default_tool_catalog
    from git_metadata_extractor.agents.models import ProviderSet
    from git_metadata_extractor.agents.tool_catalog import ToolContext

    # Captured by executing the old inline builders at HEAD, not the new catalog.
    expected = json.loads(
        (Path(__file__).parent / "fixtures/tool_defaults.json").read_text(),
    )
    context = ToolContext(
        providers=ProviderSet(**{name: object() for name in ProviderSet.model_fields if name != "sources"}),
    )
    catalog = default_tool_catalog()
    for role, names in expected.items():
        assert [t.name for t in catalog.build(role, context)] == names


def test_direct_tool_scope_cannot_reuse_a_different_toolsets_verdict(tmp_path):
    from git_metadata_extractor.agents.llm._verdict_cache import (
        get_cached_agent_verdict,
        store_agent_verdict,
    )
    from git_metadata_extractor.agents.llm.tool_defaults import default_tool_catalog
    from git_metadata_extractor.agents.models import AgentResult
    from git_metadata_extractor.agents.tool_catalog import ToolSelection, tool_scope
    from git_metadata_extractor.providers.cache import ProviderCache

    cache = ProviderCache(tmp_path / "cache.db")
    catalog = default_tool_catalog()
    identity = {"full_name": "owner/repo"}
    with tool_scope(catalog, {}):
        store_agent_verdict(
            cache,
            agent_name="repository",
            identity=identity,
            result=AgentResult(data={"id": "repo"}),
        )
        assert (
            get_cached_agent_verdict(cache, agent_name="repository", identity=identity)
            is not None
        )
    with tool_scope(catalog, {"repo_agent": ToolSelection(include_defaults=False)}):
        assert (
            get_cached_agent_verdict(cache, agent_name="repository", identity=identity)
            is None
        )
