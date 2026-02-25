from __future__ import annotations

import asyncio
from time import monotonic
from typing import Any

from src.v2.agents import ProviderSet
from src.v2.agents.models import AgentResult
from src.v2.detection.models import GitHubURLClassification, GitHubURLType
from src.v2.pipeline import PipelineOrchestrator
from src.v2.pipeline.stages.models import ContextBundle
from src.v2.providers.mock_github import MockGitHubProvider

EXPECTED_PARALLEL_AGENT_COUNT = 2
MAX_PARALLEL_START_DELTA_SECONDS = 0.04
EXPECTED_TYPED_BUCKET_KEYS = {
    "repositories",
    "persons",
    "organizations",
    "articles",
    "memberships",
    "contributions",
}


def _classification() -> GitHubURLClassification:
    return GitHubURLClassification(
        normalized_url="https://github.com/octocat/Hello-World",
        detected_type=GitHubURLType.REPOSITORY,
        owner="octocat",
        repo="Hello-World",
    )


def _providers() -> ProviderSet:
    return ProviderSet(github=MockGitHubProvider())


def test_execute_pipeline_completes_full_repository_plan() -> None:
    async def _context_gatherer(
        _detected_type: str,
        _url_info: GitHubURLClassification,
        _providers: ProviderSet,
    ) -> ContextBundle:
        return ContextBundle(
            detected_type="repository",
            context={
                "repository": {
                    "full_name": "octocat/Hello-World",
                    "metadata": {"owner": {"login": "github", "type": "Organization"}},
                    "contributors": [{"login": "alice"}, {"login": "bob"}],
                    "languages": {"Python": 1},
                    "readme_content": "README",
                },
            },
        )

    async def _repo_agent(
        _context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        return AgentResult(data={"id": "repo-root"})

    async def _person_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        return AgentResult(
            data={
                "id": context["username"],
                "repo_visible": "repo_agent" in context["pipeline_outputs"],
            },
        )

    async def _org_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        return AgentResult(data={"id": context["org_name"]})

    orchestrator = PipelineOrchestrator(
        context_gatherer=_context_gatherer,
        agent_runners={
            "repo_agent": _repo_agent,
            "person_agent": _person_agent,
            "org_agent": _org_agent,
        },
        retry_backoff_base=0,
    )
    plan = orchestrator.get_execution_plan("repository")

    result = asyncio.run(
        orchestrator.execute(
            plan=plan,
            providers=_providers(),
            context={"url_info": _classification(), "source_url": "https://github.com/octocat/Hello-World"},
        ),
    )

    assert result.errors == []
    assert result.stages_completed == [
        "context_gather",
        "repo_agent",
        "person_agents",
        "org_agents",
    ]
    assert "repo_agent" in result.agent_results
    assert "person_agent:alice" in result.agent_results
    assert "person_agent:bob" in result.agent_results
    assert "org_agent:github" in result.agent_results
    assert result.agent_results["person_agent:alice"].data["repo_visible"] is True

    typed_buckets = result.resolved_typed_entity_buckets().to_dict()
    assert set(typed_buckets) == EXPECTED_TYPED_BUCKET_KEYS
    assert [entity["id"] for entity in typed_buckets["repositories"]] == ["repo-root"]
    assert [entity["id"] for entity in typed_buckets["persons"]] == ["alice", "bob"]
    assert [entity["id"] for entity in typed_buckets["organizations"]] == ["github"]
    assert typed_buckets["articles"] == []
    assert typed_buckets["memberships"] == []
    assert typed_buckets["contributions"] == []

    serialized_result = result.to_dict()
    assert serialized_result["typed_entity_buckets"] == typed_buckets


def test_execute_runs_agents_within_stage_concurrently() -> None:
    start_times: list[float] = []

    async def _context_gatherer(
        _detected_type: str,
        _url_info: GitHubURLClassification,
        _providers: ProviderSet,
    ) -> ContextBundle:
        return ContextBundle(
            detected_type="repository",
            context={
                "repository": {
                    "full_name": "octocat/Hello-World",
                    "metadata": {"owner": {"login": "github", "type": "Organization"}},
                    "contributors": [{"login": "alice"}, {"login": "bob"}],
                    "languages": {},
                    "readme_content": "README",
                },
            },
        )

    async def _repo_agent(
        _context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        return AgentResult(data={"id": "repo-root"})

    async def _person_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        del context
        start_times.append(monotonic())
        await asyncio.sleep(0.05)
        return AgentResult(data={"id": "person"})

    async def _org_agent(
        _context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        return AgentResult(data={"id": "org"})

    orchestrator = PipelineOrchestrator(
        context_gatherer=_context_gatherer,
        agent_runners={
            "repo_agent": _repo_agent,
            "person_agent": _person_agent,
            "org_agent": _org_agent,
        },
        retry_backoff_base=0,
    )
    plan = orchestrator.get_execution_plan("repository")

    asyncio.run(
        orchestrator.execute(
            plan=plan,
            providers=_providers(),
            context={"url_info": _classification(), "source_url": "https://github.com/octocat/Hello-World"},
        ),
    )

    assert len(start_times) == EXPECTED_PARALLEL_AGENT_COUNT
    assert abs(start_times[0] - start_times[1]) < MAX_PARALLEL_START_DELTA_SECONDS


def test_execute_collects_partial_failures_without_blocking_siblings() -> None:
    async def _context_gatherer(
        _detected_type: str,
        _url_info: GitHubURLClassification,
        _providers: ProviderSet,
    ) -> ContextBundle:
        return ContextBundle(
            detected_type="repository",
            context={
                "repository": {
                    "full_name": "octocat/Hello-World",
                    "metadata": {"owner": {"login": "github", "type": "Organization"}},
                    "contributors": [{"login": "alice"}, {"login": "bob"}],
                    "languages": {},
                    "readme_content": "README",
                },
            },
        )

    async def _repo_agent(
        _context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        await asyncio.sleep(0.01)
        return AgentResult(data={"id": "repo-root"})

    async def _person_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        if context["username"] == "alice":
            message = "alice failed"
            raise RuntimeError(message)
        return AgentResult(data={"id": context["username"]})

    async def _org_agent(
        _context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        return AgentResult(data={"id": "org"})

    orchestrator = PipelineOrchestrator(
        context_gatherer=_context_gatherer,
        agent_runners={
            "repo_agent": _repo_agent,
            "person_agent": _person_agent,
            "org_agent": _org_agent,
        },
        retry_backoff_base=0,
        retry_max_retries=1,
    )
    plan = orchestrator.get_execution_plan("repository")

    result = asyncio.run(
        orchestrator.execute(
            plan=plan,
            providers=_providers(),
            context={"url_info": _classification(), "source_url": "https://github.com/octocat/Hello-World"},
        ),
    )

    assert "person_agent:alice" in result.agent_results
    assert "person_agent:bob" in result.agent_results
    assert result.agent_results["person_agent:alice"].is_partial is True
    assert result.agent_results["person_agent:bob"].is_partial is False
    assert result.duration_ms > 0


def test_repository_org_fanout_marks_direct_and_membership_contexts() -> None:
    captured_org_contexts: list[dict[str, Any]] = []

    async def _context_gatherer(
        _detected_type: str,
        _url_info: GitHubURLClassification,
        _providers: ProviderSet,
    ) -> ContextBundle:
        return ContextBundle(
            detected_type="repository",
            context={
                "repository": {
                    "full_name": "owner-org/source-repo",
                    "metadata": {"owner": {"login": "owner-org", "type": "Organization"}},
                    "contributors": [{"login": "alice"}],
                    "languages": {"Python": 1},
                    "readme_content": "README",
                },
            },
        )

    async def _repo_agent(
        _context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        return AgentResult(data={"id": "repo-root"})

    async def _person_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        del context
        return AgentResult(
            data={
                "id": "alice",
                "org:hasMembership": [
                    "alice_EPFL",
                    "alice_owner-org",
                ],
            },
        )

    async def _org_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        captured_org_contexts.append(dict(context))
        return AgentResult(data={"id": context["org_name"]})

    orchestrator = PipelineOrchestrator(
        context_gatherer=_context_gatherer,
        agent_runners={
            "repo_agent": _repo_agent,
            "person_agent": _person_agent,
            "org_agent": _org_agent,
        },
        retry_backoff_base=0,
    )
    plan = orchestrator.get_execution_plan("repository")

    asyncio.run(
        orchestrator.execute(
            plan=plan,
            providers=_providers(),
            context={"url_info": _classification(), "source_url": "https://github.com/octocat/Hello-World"},
        ),
    )

    contexts_by_org = {
        context["org_name"]: context
        for context in captured_org_contexts
    }
    owner_context = contexts_by_org["owner-org"]
    assert owner_context["github_lookup_enabled"] is True
    assert owner_context["source_repositories"] == ["owner-org/source-repo"]

    epfl_context = contexts_by_org["EPFL"]
    assert epfl_context["github_lookup_enabled"] is False
    assert "source_repositories" not in epfl_context


def test_repository_person_fanout_ignores_non_github_hash_logins() -> None:
    async def _context_gatherer(
        _detected_type: str,
        _url_info: GitHubURLClassification,
        _providers: ProviderSet,
    ) -> ContextBundle:
        return ContextBundle(
            detected_type="repository",
            context={
                "repository": {
                    "full_name": "octocat/Hello-World",
                    "metadata": {"owner": {"login": "github", "type": "Organization"}},
                    "contributors": [
                        {
                            "login": "0b3f69f4d1e5bdc420e7bea74e3e037ab841e385aefc2c37097f40edd13f8cd4",
                        },
                        {"login": "octocat"},
                    ],
                    "languages": {"Python": 1},
                    "readme_content": "README",
                },
            },
        )

    async def _repo_agent(
        _context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        return AgentResult(data={"id": "repo-root"})

    async def _person_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        return AgentResult(data={"id": context["username"]})

    async def _org_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        return AgentResult(data={"id": context["org_name"]})

    orchestrator = PipelineOrchestrator(
        context_gatherer=_context_gatherer,
        agent_runners={
            "repo_agent": _repo_agent,
            "person_agent": _person_agent,
            "org_agent": _org_agent,
        },
        retry_backoff_base=0,
    )
    plan = orchestrator.get_execution_plan("repository")

    result = asyncio.run(
        orchestrator.execute(
            plan=plan,
            providers=_providers(),
            context={"url_info": _classification(), "source_url": "https://github.com/octocat/Hello-World"},
        ),
    )

    assert "person_agent:octocat" in result.agent_results
    assert (
        "person_agent:0b3f69f4d1e5bdc420e7bea74e3e037ab841e385aefc2c37097f40edd13f8cd4"
        not in result.agent_results
    )
