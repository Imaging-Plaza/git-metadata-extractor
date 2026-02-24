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
