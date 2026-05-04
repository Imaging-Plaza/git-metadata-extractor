from __future__ import annotations

import asyncio
import json
from time import monotonic
from typing import Any

import pytest

from src.v2.agents import ProviderSet
from src.v2.agents.models import AgentResult
from src.v2.ingest.detection.models import GitHubURLClassification, GitHubURLType
from src.v2.pipeline import PipelineOrchestrator
from src.v2.pipeline.stages.models import ContextBundle
from src.v2.ingest.providers.mock_github import MockGitHubProvider

EXPECTED_PARALLEL_AGENT_COUNT = 2
MAX_PARALLEL_START_DELTA_SECONDS = 0.04
EXPECTED_BOB_RETRY_ATTEMPTS = 2
EXPECTED_REPOSITORY_STAGE_ORDER = [
    "context_gather",
    "repo_agent",
    "person_agents",
    "org_agents",
    "article_agents",
    "membership_agents",
    "contribution_agents",
]
EXPECTED_TYPED_BUCKET_KEYS = {
    "repositories",
    "persons",
    "organizations",
    "articles",
    "memberships",
    "contributions",
}
STAGE_ARTICLE_AGENT = "article_agent"
STAGE_MEMBERSHIP_AGENT = "membership_agent"
STAGE_CONTRIBUTION_AGENT = "contribution_agent"


def _classification() -> GitHubURLClassification:
    return GitHubURLClassification(
        normalized_url="https://github.com/octocat/Hello-World",
        detected_type=GitHubURLType.REPOSITORY,
        owner="octocat",
        repo="Hello-World",
    )


def _providers() -> ProviderSet:
    return ProviderSet(github=MockGitHubProvider())


def _empty_class_agent_runners() -> dict[str, Any]:
    async def _article_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        seed = context.get("article_seed")
        return AgentResult(
            data={
                "id": f"noop-article:{seed}" if isinstance(seed, str) else "noop-article",
                "entity_type": "article",
            },
        )

    async def _membership_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        seed = context.get("membership_seed")
        return AgentResult(
            data={
                "id": (
                    f"noop-membership:{seed}"
                    if isinstance(seed, str)
                    else "noop-membership"
                ),
                "entity_type": "membership",
            },
        )

    async def _contribution_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        seed = context.get("contribution_seed")
        return AgentResult(
            data={
                "id": (
                    f"noop-contribution:{seed}"
                    if isinstance(seed, str)
                    else "noop-contribution"
                ),
                "entity_type": "contribution",
            },
        )

    return {
        STAGE_ARTICLE_AGENT: _article_agent,
        STAGE_MEMBERSHIP_AGENT: _membership_agent,
        STAGE_CONTRIBUTION_AGENT: _contribution_agent,
    }


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

    async def _article_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        return AgentResult(
            data={
                "id": f"article:{context['article_seed']}",
                "entity_type": "article",
            },
        )

    async def _membership_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        return AgentResult(
            data={
                "id": f"membership:{context['membership_seed']}",
                "entity_type": "membership",
            },
        )

    async def _contribution_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        return AgentResult(
            data={
                "id": f"contribution:{context['contribution_seed']}",
                "entity_type": "contribution",
            },
        )

    orchestrator = PipelineOrchestrator(
        context_gatherer=_context_gatherer,
        agent_runners={
            "repo_agent": _repo_agent,
            "person_agent": _person_agent,
            "org_agent": _org_agent,
            STAGE_ARTICLE_AGENT: _article_agent,
            STAGE_MEMBERSHIP_AGENT: _membership_agent,
            STAGE_CONTRIBUTION_AGENT: _contribution_agent,
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
    assert result.stages_completed == EXPECTED_REPOSITORY_STAGE_ORDER
    assert "repo_agent" in result.agent_results
    assert "person_agent:alice" in result.agent_results
    assert "person_agent:bob" in result.agent_results
    assert "org_agent:github" in result.agent_results
    assert "article_agent:octocat/Hello-World" in result.agent_results
    assert "membership_agent:alice" in result.agent_results
    assert "membership_agent:bob" in result.agent_results
    assert "contribution_agent:alice_repo-root" in result.agent_results
    assert "contribution_agent:bob_repo-root" in result.agent_results
    assert result.agent_results["person_agent:alice"].data["repo_visible"] is True

    typed_buckets = result.resolved_typed_entity_buckets().to_dict()
    assert set(typed_buckets) == EXPECTED_TYPED_BUCKET_KEYS
    assert [entity["id"] for entity in typed_buckets["repositories"]] == ["repo-root"]
    assert [entity["id"] for entity in typed_buckets["persons"]] == ["alice", "bob"]
    assert [entity["id"] for entity in typed_buckets["organizations"]] == ["github"]
    assert [entity["id"] for entity in typed_buckets["articles"]] == [
        "article:octocat/Hello-World",
    ]
    assert [entity["id"] for entity in typed_buckets["memberships"]] == [
        "membership:alice",
        "membership:bob",
    ]
    assert [entity["id"] for entity in typed_buckets["contributions"]] == [
        "contribution:repo-root",
    ]

    serialized_result = result.to_dict()
    assert serialized_result["typed_entity_buckets"] == typed_buckets


def test_execute_can_append_upstream_json_and_verbatim_prompt_text_to_agent_context() -> None:
    captured_person_contexts: list[dict[str, Any]] = []
    captured_org_contexts: list[dict[str, Any]] = []
    prompt_appendix = "FILE_A\nFILE_B\nThis is verbatim."

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
        captured_person_contexts.append(dict(context))
        return AgentResult(
            data={
                "id": "alice",
                "org:hasMembership": ["alice_github"],
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
            **_empty_class_agent_runners(),
        },
        retry_backoff_base=0,
        include_upstream_stage_outputs_in_prompt=True,
        user_prompt_appendix=prompt_appendix,
    )
    plan = orchestrator.get_execution_plan("repository")

    result = asyncio.run(
        orchestrator.execute(
            plan=plan,
            providers=_providers(),
            context={
                "url_info": _classification(),
                "source_url": "https://github.com/octocat/Hello-World",
            },
        ),
    )

    assert len(captured_person_contexts) == 1
    person_context = captured_person_contexts[0]
    person_upstream = person_context.get("upstream_stage_outputs_json")
    assert isinstance(person_upstream, str)
    assert json.loads(person_upstream) == {"repo_agent": {"id": "repo-root"}}
    assert person_context.get("user_prompt_appendix") == prompt_appendix

    assert len(captured_org_contexts) == 1
    org_context = captured_org_contexts[0]
    org_upstream = org_context.get("upstream_stage_outputs_json")
    assert isinstance(org_upstream, str)
    assert json.loads(org_upstream) == {
        "repo_agent": {"id": "repo-root"},
        "person_agent:alice": {
            "id": "alice",
            "org:hasMembership": ["alice_github"],
        },
    }
    assert org_context.get("user_prompt_appendix") == prompt_appendix


def test_execute_includes_upstream_stage_outputs_in_prompt_context_by_default() -> None:
    captured_person_contexts: list[dict[str, Any]] = []

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
                    "metadata": {"owner": {"login": "octocat", "type": "User"}},
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
        captured_person_contexts.append(dict(context))
        return AgentResult(data={"id": context["username"]})

    orchestrator = PipelineOrchestrator(
        context_gatherer=_context_gatherer,
        agent_runners={
            "repo_agent": _repo_agent,
            "person_agent": _person_agent,
            **_empty_class_agent_runners(),
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

    # Two person fanouts: one for the listed contributor `alice`, one
    # for the User-type owner `octocat` materialised by the orchestrator.
    assert len(captured_person_contexts) == 2
    assert {ctx["username"] for ctx in captured_person_contexts} == {"alice", "octocat"}
    for captured in captured_person_contexts:
        upstream_json = captured.get("upstream_stage_outputs_json")
        assert isinstance(upstream_json, str)
        assert json.loads(upstream_json) == {"repo_agent": {"id": "repo-root"}}


def test_execute_skips_github_organization_accounts_from_person_fanout() -> None:
    class _GitHubProviderWithOrgContributor(MockGitHubProvider):
        def get_user(self, username: str) -> dict[str, Any]:
            if username == "alice":
                return {"login": "alice", "type": "User", "name": "Alice"}
            if username == "sdsc-ordes":
                return {
                    "login": "sdsc-ordes",
                    "type": "Organization",
                    "name": "Swiss Data Science Center - ORD",
                }
            return super().get_user(username)

    async def _context_gatherer(
        _detected_type: str,
        _url_info: GitHubURLClassification,
        _providers: ProviderSet,
    ) -> ContextBundle:
        return ContextBundle(
            detected_type="repository",
                context={
                    "repository": {
                        "full_name": "sdsc-ordes/gimie",
                        "metadata": {"owner": {"login": "octocat", "type": "User"}},
                        "contributors": [{"login": "alice"}, {"login": "sdsc-ordes"}],
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
            **_empty_class_agent_runners(),
        },
        retry_backoff_base=0,
    )
    plan = orchestrator.get_execution_plan("repository")

    result = asyncio.run(
        orchestrator.execute(
            plan=plan,
            providers=ProviderSet(github=_GitHubProviderWithOrgContributor()),
            context={"url_info": _classification(), "source_url": "https://github.com/sdsc-ordes/gimie"},
        ),
    )

    assert "person_agent:alice" in result.agent_results
    assert "person_agent:sdsc-ordes" not in result.agent_results
    assert any(
        warning == "Skipping person fanout for GitHub organization account: sdsc-ordes"
        for warning in result.warnings
    )


def test_execute_skips_repository_owner_org_without_user_lookup() -> None:
    called_usernames: list[str] = []

    class _GitHubProviderWithUserLookupTracking(MockGitHubProvider):
        def get_user(self, username: str) -> dict[str, Any]:
            called_usernames.append(username)
            if username == "alice":
                return {"login": "alice", "type": "User", "name": "Alice"}
            if username == "sdsc-ordes":
                raise AssertionError("repository owner org should be filtered before user lookup")
            return super().get_user(username)

    async def _context_gatherer(
        _detected_type: str,
        _url_info: GitHubURLClassification,
        _providers: ProviderSet,
    ) -> ContextBundle:
        return ContextBundle(
            detected_type="repository",
            context={
                "repository": {
                    "full_name": "sdsc-ordes/gimie",
                    "metadata": {"owner": {"login": "SDSC-ORDES", "type": "Organization"}},
                    "contributors": [{"login": "alice"}, {"login": "sdsc-ordes"}],
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

    orchestrator = PipelineOrchestrator(
        context_gatherer=_context_gatherer,
        agent_runners={
            "repo_agent": _repo_agent,
            "person_agent": _person_agent,
            **_empty_class_agent_runners(),
        },
        retry_backoff_base=0,
    )
    plan = orchestrator.get_execution_plan("repository")

    result = asyncio.run(
        orchestrator.execute(
            plan=plan,
            providers=ProviderSet(github=_GitHubProviderWithUserLookupTracking()),
            context={"url_info": _classification(), "source_url": "https://github.com/sdsc-ordes/gimie"},
        ),
    )

    assert "person_agent:alice" in result.agent_results
    assert "person_agent:sdsc-ordes" not in result.agent_results
    assert "sdsc-ordes" not in called_usernames


def test_execute_accepts_empty_class_agent_payload_and_uses_stats_entities() -> None:
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
                    "metadata": {"owner": {"login": "octocat", "type": "User"}},
                    "contributors": [],
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

    async def _article_agent(
        _context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        return AgentResult(
            data={},
            warnings=["No article queries could be derived from runtime context"],
            stats={
                "articles": [
                    {"id": "article:stats-only", "entity_type": "article"},
                ],
            },
        )

    orchestrator = PipelineOrchestrator(
        context_gatherer=_context_gatherer,
        agent_runners={
            **_empty_class_agent_runners(),
            "repo_agent": _repo_agent,
            STAGE_ARTICLE_AGENT: _article_agent,
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

    assert "article_agent:octocat/Hello-World" in result.agent_results
    assert "article_agent:octocat/Hello-World: Agent returned empty or invalid data payload" not in result.warnings
    typed_buckets = result.resolved_typed_entity_buckets().to_dict()
    assert [entity["id"] for entity in typed_buckets["articles"]] == ["article:stats-only"]


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
            **_empty_class_agent_runners(),
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
            **_empty_class_agent_runners(),
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
            **_empty_class_agent_runners(),
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
    assert "repository_context" in owner_context

    epfl_context = contexts_by_org["EPFL"]
    assert epfl_context["github_lookup_enabled"] is False
    assert epfl_context["source_repositories"] == ["owner-org/source-repo"]
    assert "repository_context" in epfl_context


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
            **_empty_class_agent_runners(),
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


def test_class_stage_work_items_include_upstream_references_and_typed_buckets() -> None:
    captured_article_contexts: list[dict[str, Any]] = []
    captured_membership_contexts: list[dict[str, Any]] = []
    captured_contribution_contexts: list[dict[str, Any]] = []

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
        return AgentResult(
            data={"id": "repo-root"},
            stats={
                "derivation": {
                    "repository_full_name": "repo-root",
                    "contributors": [
                        {"login": "alice", "contributions": 3},
                        {"login": "bob", "contributions": 1},
                    ],
                },
            },
        )

    async def _person_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        username = context["username"]
        return AgentResult(
            data={"id": username, "schema:name": username.title()},
            stats={
                "derivation": {
                    "person_id": username,
                    "affiliation_names": ["EPFL"],
                    "orcid_affiliations": [
                        {
                            "organization": "EPFL",
                            "role": "Research Engineer",
                            "start_date": "2021-01-01",
                            "end_date": None,
                        },
                    ],
                },
            },
        )

    async def _org_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        return AgentResult(data={"id": context["org_name"], "schema:name": context["org_name"]})

    async def _article_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        captured_article_contexts.append(dict(context))
        return AgentResult(data={"id": f"article:{context['article_seed']}", "entity_type": "article"})

    async def _membership_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        captured_membership_contexts.append(dict(context))
        return AgentResult(data={"id": f"membership:{context['membership_seed']}", "entity_type": "membership"})

    async def _contribution_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        captured_contribution_contexts.append(dict(context))
        return AgentResult(
            data={"id": f"contribution:{context['contribution_seed']}", "entity_type": "contribution"},
        )

    orchestrator = PipelineOrchestrator(
        context_gatherer=_context_gatherer,
        agent_runners={
            "repo_agent": _repo_agent,
            "person_agent": _person_agent,
            "org_agent": _org_agent,
            STAGE_ARTICLE_AGENT: _article_agent,
            STAGE_MEMBERSHIP_AGENT: _membership_agent,
            STAGE_CONTRIBUTION_AGENT: _contribution_agent,
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

    assert [context["article_seed"] for context in captured_article_contexts] == [
        "octocat/Hello-World",
    ]
    assert sorted(context["membership_seed"] for context in captured_membership_contexts) == [
        "alice",
        "bob",
    ]
    # One contribution per (person, repo) pair: alice→repo-root and bob→repo-root.
    assert [context["contribution_seed"] for context in captured_contribution_contexts] == [
        "repo-root",
        "repo-root",
    ]

    membership_context = captured_membership_contexts[0]
    assert membership_context["known_persons"][0]["affiliations"] == ["EPFL"]
    assert membership_context["target_person"]["id"] == membership_context["membership_seed"]
    assert isinstance(membership_context["target_organizations"], list)
    assert membership_context["target_organizations"]
    assert isinstance(membership_context["typed_entity_buckets"], dict)
    assert membership_context["typed_entity_buckets"]["persons"]

    contribution_context = captured_contribution_contexts[0]
    assert contribution_context["known_repositories"][0]["contributors"] == [
        {"login": "alice", "contributions": 3},
        {"login": "bob", "contributions": 1},
    ]


def test_class_stage_fanout_is_deterministic_for_user_and_organization_roots() -> None:
    captured_article_seeds: list[str] = []
    captured_membership_seeds: list[str] = []
    captured_contribution_seeds: list[str] = []

    async def _context_gatherer(
        detected_type: str,
        _url_info: GitHubURLClassification,
        _providers: ProviderSet,
    ) -> ContextBundle:
        if detected_type == "user":
            return ContextBundle(
                detected_type="user",
                context={
                    "user": {
                        "username": "octocat",
                        "profile": {"company": "github"},
                        "owned_repos": ["octocat/repo-a", "repo-b"],
                    },
                },
            )
        return ContextBundle(
            detected_type="organization",
            context={
                "organization": {
                    "org_name": "github",
                    "members": ["alice", "bob"],
                    "owned_repos": ["github/repo-a"],
                },
            },
        )

    async def _person_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        username = context["username"]
        return AgentResult(
            data={"id": username, "schema:name": username},
            stats={"derivation": {"person_id": username, "affiliation_names": ["github"]}},
        )

    async def _repo_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        full_name = context["full_name"]
        return AgentResult(
            data={"id": full_name},
            stats={"derivation": {"repository_full_name": full_name, "contributors": []}},
        )

    async def _org_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        return AgentResult(data={"id": context["org_name"], "schema:name": context["org_name"]})

    async def _article_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        captured_article_seeds.append(context["article_seed"])
        return AgentResult(
            data={
                "id": f"article:{context['article_seed']}",
                "entity_type": "article",
            },
        )

    async def _membership_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        captured_membership_seeds.append(context["membership_seed"])
        return AgentResult(
            data={
                "id": f"membership:{context['membership_seed']}",
                "entity_type": "membership",
            },
        )

    async def _contribution_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        captured_contribution_seeds.append(context["contribution_seed"])
        return AgentResult(
            data={
                "id": f"contribution:{context['contribution_seed']}",
                "entity_type": "contribution",
            },
        )

    orchestrator = PipelineOrchestrator(
        context_gatherer=_context_gatherer,
        agent_runners={
            "repo_agent": _repo_agent,
            "person_agent": _person_agent,
            "org_agent": _org_agent,
            STAGE_ARTICLE_AGENT: _article_agent,
            STAGE_MEMBERSHIP_AGENT: _membership_agent,
            STAGE_CONTRIBUTION_AGENT: _contribution_agent,
        },
        retry_backoff_base=0,
    )

    user_plan = orchestrator.get_execution_plan("user")
    organization_plan = orchestrator.get_execution_plan("organization")

    asyncio.run(
        orchestrator.execute(
            plan=user_plan,
            providers=_providers(),
            context={
                "url_info": GitHubURLClassification(
                    normalized_url="https://github.com/octocat",
                    detected_type=GitHubURLType.USER,
                    owner="octocat",
                    repo=None,
                ),
                "source_url": "https://github.com/octocat",
            },
        ),
    )
    asyncio.run(
        orchestrator.execute(
            plan=organization_plan,
            providers=_providers(),
            context={
                "url_info": GitHubURLClassification(
                    normalized_url="https://github.com/orgs/github",
                    detected_type=GitHubURLType.ORGANIZATION,
                    owner="github",
                    repo=None,
                ),
                "source_url": "https://github.com/orgs/github",
            },
        ),
    )

    assert captured_article_seeds == ["octocat", "github"]
    assert captured_membership_seeds == ["octocat", "alice", "bob"]
    # In the org run, both `alice` and `bob` produce a contribution
    # against `github/repo-a` (one (person, repo) pair each).
    assert captured_contribution_seeds == [
        "octocat/repo-a",
        "octocat/repo-b",
        "github/repo-a",
        "github/repo-a",
    ]


def test_membership_context_prioritizes_target_organizations_from_person_links() -> None:
    captured_membership_contexts: list[dict[str, Any]] = []

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
        return AgentResult(
            data={"id": "repo-root"},
            stats={"derivation": {"repository_full_name": "repo-root", "contributors": []}},
        )

    async def _person_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        username = context["username"]
        return AgentResult(
            data={
                "id": username,
                "schema:name": "Alice",
                "affiliations": ["EPFL"],
                "org:hasMembership": [f"{username}_EPFL"],
            },
            stats={"derivation": {"person_id": username, "affiliation_names": ["EPFL"]}},
        )

    async def _org_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        return AgentResult(data={"id": context["org_name"], "schema:name": context["org_name"]})

    async def _membership_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        captured_membership_contexts.append(dict(context))
        return AgentResult(
            data={"id": f"membership:{context['membership_seed']}", "entity_type": "membership"},
        )

    orchestrator = PipelineOrchestrator(
        context_gatherer=_context_gatherer,
        agent_runners={
            "repo_agent": _repo_agent,
            "person_agent": _person_agent,
            "org_agent": _org_agent,
            STAGE_MEMBERSHIP_AGENT: _membership_agent,
            **{
                key: value
                for key, value in _empty_class_agent_runners().items()
                if key != STAGE_MEMBERSHIP_AGENT
            },
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

    assert len(captured_membership_contexts) == 1
    membership_context = captured_membership_contexts[0]
    target_organization_ids = [
        organization.get("id")
        for organization in membership_context["target_organizations"]
        if isinstance(organization, dict)
    ]
    assert target_organization_ids == ["EPFL"]


def test_class_stage_partial_failures_keep_sibling_and_downstream_execution() -> None:
    attempt_count: dict[str, int] = {}

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
        return AgentResult(
            data={"id": "repo-root"},
            stats={"derivation": {"repository_full_name": "repo-root", "contributors": []}},
        )

    async def _person_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        username = context["username"]
        return AgentResult(
            data={"id": username},
            stats={"derivation": {"person_id": username, "affiliation_names": ["github"]}},
        )

    async def _org_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        return AgentResult(data={"id": context["org_name"], "schema:name": context["org_name"]})

    async def _article_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        return AgentResult(data={"id": f"article:{context['article_seed']}", "entity_type": "article"})

    async def _membership_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        seed = context["membership_seed"]
        attempt_count[seed] = attempt_count.get(seed, 0) + 1
        if seed == "alice":
            failure_message = "alice membership failed"
            raise RuntimeError(failure_message)
        if seed == "bob" and attempt_count[seed] == 1:
            retry_message = "bob membership transient failure"
            raise RuntimeError(retry_message)
        return AgentResult(data={"id": f"membership:{seed}", "entity_type": "membership"})

    async def _contribution_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        return AgentResult(
            data={"id": f"contribution:{context['contribution_seed']}", "entity_type": "contribution"},
        )

    orchestrator = PipelineOrchestrator(
        context_gatherer=_context_gatherer,
        agent_runners={
            "repo_agent": _repo_agent,
            "person_agent": _person_agent,
            "org_agent": _org_agent,
            STAGE_ARTICLE_AGENT: _article_agent,
            STAGE_MEMBERSHIP_AGENT: _membership_agent,
            STAGE_CONTRIBUTION_AGENT: _contribution_agent,
        },
        retry_backoff_base=0,
        retry_max_retries=2,
    )
    plan = orchestrator.get_execution_plan("repository")

    result = asyncio.run(
        orchestrator.execute(
            plan=plan,
            providers=_providers(),
            context={"url_info": _classification(), "source_url": "https://github.com/octocat/Hello-World"},
        ),
    )

    assert result.agent_results["membership_agent:alice"].is_partial is True
    assert result.agent_results["membership_agent:bob"].is_partial is False
    assert result.agent_results["contribution_agent:alice_repo-root"].is_partial is False
    assert attempt_count["bob"] == EXPECTED_BOB_RETRY_ATTEMPTS


def test_execute_uses_llm_repository_runner_for_repository_runtime() -> None:
    llm_repo_calls = 0
    llm_org_calls = 0
    llm_article_calls = 0
    rule_repo_calls = 0

    class _LLMRepositoryRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del context, providers
            nonlocal llm_repo_calls
            llm_repo_calls += 1
            return AgentResult(data={"id": "llm-repo-root"})

    class _LLMOrganizationRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del providers
            nonlocal llm_org_calls
            llm_org_calls += 1
            return AgentResult(data={"id": f"llm-org:{context['org_name']}"})

    class _LLMArticleRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del providers
            nonlocal llm_article_calls
            llm_article_calls += 1
            return AgentResult(
                data={
                    "id": f"llm-article:{context.get('article_seed', 'seed')}",
                    "entity_type": "article",
                },
            )

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
                    "contributors": [],
                    "languages": {"Python": 1},
                    "readme_content": "README",
                },
            },
        )

    async def _rule_repo_agent(
        _context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        nonlocal rule_repo_calls
        rule_repo_calls += 1
        return AgentResult(data={"id": "rule-repo-root"})

    async def _org_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        return AgentResult(data={"id": context["org_name"]})

    orchestrator = PipelineOrchestrator(
        context_gatherer=_context_gatherer,
        llm_repository_agent=_LLMRepositoryRunner(),
        llm_organization_agent=_LLMOrganizationRunner(),
        llm_article_agent=_LLMArticleRunner(),
        llm_membership_agent=_LLMArticleRunner(),
        llm_contribution_agent=_LLMArticleRunner(),
        agent_runners={
            "repo_agent": _rule_repo_agent,
            "org_agent": _org_agent,
            **_empty_class_agent_runners(),
        },
        retry_backoff_base=0,
    )
    plan = orchestrator.get_execution_plan("repository")

    result = asyncio.run(
        orchestrator.execute(
            plan=plan,
            providers=_providers(),
            context={
                "url_info": _classification(),
                "source_url": "https://github.com/octocat/Hello-World",
                "agent_runtime": "llm",
            },
        ),
    )

    assert llm_repo_calls == 1
    assert llm_org_calls == 1
    assert llm_article_calls == 1
    assert rule_repo_calls == 0
    assert result.agent_results["repo_agent"].data["id"] == "llm-repo-root"
    assert result.agent_results["org_agent:github"].data["id"] == "llm-org:github"


def test_execute_llm_compiles_context_summary_and_strips_raw_repository_blobs() -> None:
    summary_calls = 0
    captured_repo_contexts: list[dict[str, Any]] = []

    class _SummaryRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del providers
            nonlocal summary_calls
            summary_calls += 1
            repository_context = context["gathered_context"]["repository"]
            assert repository_context["readme_content"] == "README RAW"
            assert repository_context["gimie_jsonld"] == {"@id": "https://github.com/octocat/Hello-World"}
            return AgentResult(data={"summary_markdown": "# Compiled Context\n- fact"})

    class _LLMRepositoryRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del providers
            captured_repo_contexts.append(dict(context))
            repository_context = context.get("repository_context")
            assert isinstance(repository_context, dict)
            assert "readme_content" not in repository_context
            assert "gimie_jsonld" not in repository_context
            assert "repository_files" not in repository_context
            return AgentResult(data={"id": "llm-repo-root"})

    class _LLMNoDataRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del context, providers
            return AgentResult(data={})

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
                    "metadata": {
                        "full_name": "octocat/Hello-World",
                        "owner": {"login": "octocat", "type": "User"},
                    },
                    "contributors": [],
                    "languages": {"Python": 1},
                    "readme_content": "README RAW",
                    "gimie_jsonld": {"@id": "https://github.com/octocat/Hello-World"},
                    "repository_files": [
                        {"path": "pyproject.toml", "content": "[tool.uv]\n"},
                    ],
                },
            },
        )

    orchestrator = PipelineOrchestrator(
        context_gatherer=_context_gatherer,
        llm_context_summary_agent=_SummaryRunner(),
        llm_repository_agent=_LLMRepositoryRunner(),
        llm_person_agent=_LLMNoDataRunner(),
        llm_organization_agent=_LLMNoDataRunner(),
        llm_article_agent=_LLMNoDataRunner(),
        llm_membership_agent=_LLMNoDataRunner(),
        llm_contribution_agent=_LLMNoDataRunner(),
        retry_backoff_base=0,
    )
    plan = orchestrator.get_execution_plan("repository")

    result = asyncio.run(
        orchestrator.execute(
            plan=plan,
            providers=_providers(),
            context={
                "url_info": _classification(),
                "source_url": "https://github.com/octocat/Hello-World",
                "agent_runtime": "llm",
            },
        ),
    )

    assert summary_calls == 1
    assert len(captured_repo_contexts) == 1
    assert result.gathered_context["compiled_context_markdown"].startswith("# Compiled Context")
    prompt_appendix = captured_repo_contexts[0].get("user_prompt_appendix")
    assert isinstance(prompt_appendix, str)
    assert "## Compiled Source Summary" in prompt_appendix
    assert "# Compiled Context" in prompt_appendix


def test_execute_uses_llm_repository_runner_for_user_and_org_in_llm_mode() -> None:
    llm_repo_calls = 0
    llm_org_calls = 0
    llm_class_calls = 0
    rule_repo_calls = 0

    class _LLMRepositoryRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del context, providers
            nonlocal llm_repo_calls
            llm_repo_calls += 1
            return AgentResult(data={"id": "llm-repo-root"})

    class _LLMOrganizationRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del providers
            nonlocal llm_org_calls
            llm_org_calls += 1
            return AgentResult(data={"id": f"llm-org:{context['org_name']}"})

    class _LLMPersonRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del providers
            return AgentResult(
                data={
                    "id": context["username"],
                    "type": "schema:Person",
                    "org:hasMembership": [],
                },
            )

    class _LLMClassRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del providers
            nonlocal llm_class_calls
            llm_class_calls += 1
            if "article_seed" in context:
                return AgentResult(
                    data={
                        "id": f"llm-article:{context['article_seed']}",
                        "entity_type": "article",
                    },
                )
            if "membership_seed" in context:
                return AgentResult(
                    data={
                        "id": f"llm-membership:{context['membership_seed']}",
                        "entity_type": "membership",
                    },
                )
            return AgentResult(
                data={
                    "id": f"llm-contribution:{context.get('contribution_seed', 'seed')}",
                    "entity_type": "contribution",
                },
            )

    async def _context_gatherer(
        detected_type: str,
        _url_info: GitHubURLClassification,
        _providers: ProviderSet,
    ) -> ContextBundle:
        if detected_type == "user":
            return ContextBundle(
                detected_type="user",
                context={
                    "user": {
                        "username": "octocat",
                        "owned_repos": ["octocat/repo-a"],
                        "profile": {"company": "github"},
                    },
                },
            )
        return ContextBundle(
            detected_type="organization",
            context={
                "organization": {
                    "org_name": "github",
                    "owned_repos": ["github/repo-a"],
                    "members": ["alice"],
                },
            },
        )

    async def _person_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        return AgentResult(
            data={"id": context["username"]},
            stats={"derivation": {"person_id": context["username"], "affiliation_names": []}},
        )

    async def _rule_repo_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        nonlocal rule_repo_calls
        rule_repo_calls += 1
        return AgentResult(data={"id": context["full_name"]})

    async def _org_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        return AgentResult(data={"id": context["org_name"]})

    orchestrator = PipelineOrchestrator(
        context_gatherer=_context_gatherer,
        llm_repository_agent=_LLMRepositoryRunner(),
        llm_person_agent=_LLMPersonRunner(),
        llm_organization_agent=_LLMOrganizationRunner(),
        llm_article_agent=_LLMClassRunner(),
        llm_membership_agent=_LLMClassRunner(),
        llm_contribution_agent=_LLMClassRunner(),
        agent_runners={
            "person_agent": _person_agent,
            "repo_agent": _rule_repo_agent,
            "org_agent": _org_agent,
            **_empty_class_agent_runners(),
        },
        retry_backoff_base=0,
    )

    user_plan = orchestrator.get_execution_plan("user")
    org_plan = orchestrator.get_execution_plan("organization")

    asyncio.run(
        orchestrator.execute(
            plan=user_plan,
            providers=_providers(),
            context={
                "url_info": GitHubURLClassification(
                    normalized_url="https://github.com/octocat",
                    detected_type=GitHubURLType.USER,
                    owner="octocat",
                    repo=None,
                ),
                "source_url": "https://github.com/octocat",
                "agent_runtime": "llm",
            },
        ),
    )
    asyncio.run(
        orchestrator.execute(
            plan=org_plan,
            providers=_providers(),
            context={
                "url_info": GitHubURLClassification(
                    normalized_url="https://github.com/orgs/github",
                    detected_type=GitHubURLType.ORGANIZATION,
                    owner="github",
                    repo=None,
                ),
                "source_url": "https://github.com/orgs/github",
                "agent_runtime": "llm",
            },
        ),
    )

    assert llm_repo_calls == 2
    assert llm_org_calls == 2
    assert llm_class_calls >= 2
    assert rule_repo_calls == 0


def test_execute_uses_llm_organization_runner_for_repository_runtime() -> None:
    llm_org_calls = 0
    llm_article_calls = 0
    rule_org_calls = 0

    class _LLMRepositoryRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del context, providers
            return AgentResult(data={"id": "llm-repo-root"})

    class _LLMOrganizationRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del providers
            nonlocal llm_org_calls
            llm_org_calls += 1
            return AgentResult(data={"id": f"llm-org:{context['org_name']}"})

    class _LLMArticleRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del providers
            nonlocal llm_article_calls
            llm_article_calls += 1
            return AgentResult(
                data={
                    "id": f"llm-article:{context.get('article_seed', 'seed')}",
                    "entity_type": "article",
                },
            )

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
                    "contributors": [],
                    "languages": {"Python": 1},
                    "readme_content": "README",
                },
            },
        )

    async def _rule_repo_agent(
        _context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        return AgentResult(data={"id": "rule-repo-root"})

    async def _rule_org_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        nonlocal rule_org_calls
        rule_org_calls += 1
        return AgentResult(data={"id": context["org_name"]})

    orchestrator = PipelineOrchestrator(
        context_gatherer=_context_gatherer,
        llm_repository_agent=_LLMRepositoryRunner(),
        llm_organization_agent=_LLMOrganizationRunner(),
        llm_article_agent=_LLMArticleRunner(),
        llm_membership_agent=_LLMArticleRunner(),
        llm_contribution_agent=_LLMArticleRunner(),
        agent_runners={
            "repo_agent": _rule_repo_agent,
            "org_agent": _rule_org_agent,
            **_empty_class_agent_runners(),
        },
        retry_backoff_base=0,
    )
    plan = orchestrator.get_execution_plan("repository")

    result = asyncio.run(
        orchestrator.execute(
            plan=plan,
            providers=_providers(),
            context={
                "url_info": _classification(),
                "source_url": "https://github.com/octocat/Hello-World",
                "agent_runtime": "llm",
            },
        ),
    )

    assert llm_org_calls == 1
    assert llm_article_calls == 1
    assert rule_org_calls == 0
    assert result.agent_results["org_agent:github"].data["id"] == "llm-org:github"


def test_execute_uses_llm_class_runners_for_repository_runtime() -> None:
    llm_article_calls = 0
    llm_membership_calls = 0
    llm_contribution_calls = 0
    rule_class_calls = 0

    class _LLMRepositoryRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del providers
            full_name = context.get("full_name", "octocat/Hello-World")
            return AgentResult(
                data={
                    "id": full_name,
                    "type": "schema:SoftwareSourceCode",
                    "schema:author": ["alice"],
                },
                stats={
                    "derivation": {
                        "repository_full_name": full_name,
                        "contributors": [{"login": "alice", "contributions": 3}],
                    },
                },
            )

    class _LLMPersonRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del providers
            username = context.get("username", "alice")
            return AgentResult(
                data={
                    "id": username,
                    "type": "schema:Person",
                    "schema:name": username,
                    "org:hasMembership": ["alice_github"],
                },
                stats={"derivation": {"person_id": username, "affiliation_names": ["github"]}},
            )

    class _LLMOrganizationRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del providers
            org_name = context.get("org_name", "github")
            return AgentResult(
                data={
                    "id": org_name,
                    "type": "org:Organization",
                    "schema:name": org_name,
                },
            )

    class _LLMArticleRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del providers
            nonlocal llm_article_calls
            llm_article_calls += 1
            return AgentResult(
                data={
                    "id": "10.1000/llm-article",
                    "type": "schema:ScholarlyArticle",
                    "schema:author": [context.get("article_seed", "alice")],
                },
                stats={"articles": []},
            )

    class _LLMMembershipRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del providers
            nonlocal llm_membership_calls
            llm_membership_calls += 1
            seed = context.get("membership_seed", "alice")
            return AgentResult(
                data={
                    "id": f"{seed}_github",
                    "type": "org:Membership",
                    "org:organization": "github",
                },
                stats={"memberships": []},
            )

    class _LLMContributionRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del providers
            nonlocal llm_contribution_calls
            llm_contribution_calls += 1
            seed = context.get("contribution_seed", "octocat/Hello-World")
            return AgentResult(
                data={
                    "id": f"alice_{seed}",
                    "type": "pulse:Contribution",
                    "schema:author": "alice",
                    "pulse:contributionTo": seed,
                },
                stats={"contributions": []},
            )

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
                    "contributors": [{"login": "alice"}],
                    "languages": {"Python": 1},
                    "readme_content": "README",
                },
            },
        )

    async def _rule_repo_agent(
        _context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        return AgentResult(data={"id": "rule-repo-root"})

    async def _rule_person_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        return AgentResult(data={"id": context["username"]})

    async def _rule_org_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        return AgentResult(data={"id": context["org_name"]})

    async def _rule_class_agent(
        _context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        nonlocal rule_class_calls
        rule_class_calls += 1
        return AgentResult(data={"id": "rule-class"})

    orchestrator = PipelineOrchestrator(
        context_gatherer=_context_gatherer,
        llm_repository_agent=_LLMRepositoryRunner(),
        llm_person_agent=_LLMPersonRunner(),
        llm_organization_agent=_LLMOrganizationRunner(),
        llm_article_agent=_LLMArticleRunner(),
        llm_membership_agent=_LLMMembershipRunner(),
        llm_contribution_agent=_LLMContributionRunner(),
        agent_runners={
            "repo_agent": _rule_repo_agent,
            "person_agent": _rule_person_agent,
            "org_agent": _rule_org_agent,
            "article_agent": _rule_class_agent,
            "membership_agent": _rule_class_agent,
            "contribution_agent": _rule_class_agent,
        },
        retry_backoff_base=0,
    )
    plan = orchestrator.get_execution_plan("repository")

    result = asyncio.run(
        orchestrator.execute(
            plan=plan,
            providers=_providers(),
            context={
                "url_info": _classification(),
                "source_url": "https://github.com/octocat/Hello-World",
                "agent_runtime": "llm",
            },
        ),
    )

    assert llm_article_calls == 1
    assert llm_membership_calls == 1
    assert llm_contribution_calls == 1
    assert rule_class_calls == 0
    assert "article_agent:octocat/Hello-World" in result.agent_results
    assert "membership_agent:alice" in result.agent_results
    assert "contribution_agent:alice_octocat/Hello-World" in result.agent_results


def test_execute_hard_fails_when_llm_repository_runner_errors() -> None:
    rule_repo_calls = 0

    class _FailingLLMRepositoryRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del context, providers
            raise RuntimeError("llm repository failure")

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
                    "contributors": [],
                    "languages": {"Python": 1},
                    "readme_content": "README",
                },
            },
        )

    async def _rule_repo_agent(
        _context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        nonlocal rule_repo_calls
        rule_repo_calls += 1
        return AgentResult(data={"id": "rule-repo-root"})

    async def _org_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        return AgentResult(data={"id": context["org_name"]})

    orchestrator = PipelineOrchestrator(
        context_gatherer=_context_gatherer,
        llm_repository_agent=_FailingLLMRepositoryRunner(),
        agent_runners={
            "repo_agent": _rule_repo_agent,
            "org_agent": _org_agent,
            **_empty_class_agent_runners(),
        },
        retry_backoff_base=0,
        retry_max_retries=1,
    )
    plan = orchestrator.get_execution_plan("repository")

    with pytest.raises(RuntimeError, match="LLM repository runtime failed without fallback"):
        asyncio.run(
            orchestrator.execute(
                plan=plan,
                providers=_providers(),
                context={
                    "url_info": _classification(),
                    "source_url": "https://github.com/octocat/Hello-World",
                    "agent_runtime": "llm",
                },
            ),
        )

    assert rule_repo_calls == 0


def test_execute_uses_llm_repository_runner_for_user_repo_fanout() -> None:
    llm_repo_calls = 0
    rule_repo_calls = 0

    class _LLMRepositoryRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del providers
            nonlocal llm_repo_calls
            llm_repo_calls += 1
            return AgentResult(
                data={
                    "id": context["full_name"],
                    "type": "schema:SoftwareSourceCode",
                },
            )

    class _LLMPersonRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del providers
            return AgentResult(
                data={
                    "id": context["username"],
                    "type": "schema:Person",
                    "org:hasMembership": [],
                },
            )

    class _LLMNoDataRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del context, providers
            return AgentResult(data={})

    async def _context_gatherer(
        _detected_type: str,
        _url_info: GitHubURLClassification,
        _providers: ProviderSet,
    ) -> ContextBundle:
        return ContextBundle(
            detected_type="user",
            context={
                "user": {
                    "username": "alice",
                    "profile": {"login": "alice"},
                    "owned_repos": ["alice/repo-a"],
                    "repository_contexts": {
                        "alice/repo-a": {
                            "full_name": "alice/repo-a",
                            "metadata": {"full_name": "alice/repo-a"},
                            "contributors": [],
                            "languages": {"Python": 1},
                            "readme_content": "README",
                            "gimie_jsonld": {},
                        },
                    },
                },
            },
        )

    async def _rule_repo_agent(
        _context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        nonlocal rule_repo_calls
        rule_repo_calls += 1
        return AgentResult(data={"id": "rule-repo"})

    orchestrator = PipelineOrchestrator(
        context_gatherer=_context_gatherer,
        llm_repository_agent=_LLMRepositoryRunner(),
        llm_person_agent=_LLMPersonRunner(),
        llm_organization_agent=_LLMNoDataRunner(),
        llm_article_agent=_LLMNoDataRunner(),
        llm_membership_agent=_LLMNoDataRunner(),
        llm_contribution_agent=_LLMNoDataRunner(),
        agent_runners={
            "repo_agent": _rule_repo_agent,
            "person_agent": _rule_repo_agent,
            "org_agent": _rule_repo_agent,
            **_empty_class_agent_runners(),
        },
        retry_backoff_base=0,
    )
    plan = orchestrator.get_execution_plan("user")

    result = asyncio.run(
        orchestrator.execute(
            plan=plan,
            providers=_providers(),
            context={
                "url_info": GitHubURLClassification(
                    normalized_url="https://github.com/alice",
                    detected_type=GitHubURLType.USER,
                    owner="alice",
                    repo=None,
                ),
                "source_url": "https://github.com/alice",
                "agent_runtime": "llm",
            },
        ),
    )

    assert llm_repo_calls == 1
    assert rule_repo_calls == 0
    assert "repo_agent:alice/repo-a" in result.agent_results


def test_execute_hard_fails_when_llm_user_root_runner_errors() -> None:
    rule_person_calls = 0

    class _FailingLLMPersonRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del context, providers
            raise RuntimeError("llm person failure")

    async def _context_gatherer(
        _detected_type: str,
        _url_info: GitHubURLClassification,
        _providers: ProviderSet,
    ) -> ContextBundle:
        return ContextBundle(
            detected_type="user",
            context={
                "user": {
                    "username": "alice",
                    "profile": {"login": "alice"},
                    "owned_repos": [],
                },
            },
        )

    async def _rule_person_agent(
        _context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        nonlocal rule_person_calls
        rule_person_calls += 1
        return AgentResult(data={"id": "rule-person"})

    orchestrator = PipelineOrchestrator(
        context_gatherer=_context_gatherer,
        llm_person_agent=_FailingLLMPersonRunner(),
        agent_runners={
            "person_agent": _rule_person_agent,
            "repo_agent": _rule_person_agent,
            "org_agent": _rule_person_agent,
            **_empty_class_agent_runners(),
        },
        retry_backoff_base=0,
        retry_max_retries=1,
    )
    plan = orchestrator.get_execution_plan("user")

    with pytest.raises(RuntimeError, match="LLM user runtime failed without fallback"):
        asyncio.run(
            orchestrator.execute(
                plan=plan,
                providers=_providers(),
                context={
                    "url_info": GitHubURLClassification(
                        normalized_url="https://github.com/alice",
                        detected_type=GitHubURLType.USER,
                        owner="alice",
                        repo=None,
                    ),
                    "source_url": "https://github.com/alice",
                    "agent_runtime": "llm",
                },
            ),
        )

    assert rule_person_calls == 0


def test_execute_hard_fails_when_llm_organization_root_runner_errors() -> None:
    rule_org_calls = 0

    class _FailingLLMOrganizationRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del context, providers
            raise RuntimeError("llm organization failure")

    async def _context_gatherer(
        _detected_type: str,
        _url_info: GitHubURLClassification,
        _providers: ProviderSet,
    ) -> ContextBundle:
        return ContextBundle(
            detected_type="organization",
            context={
                "organization": {
                    "org_name": "example",
                    "profile": {"login": "example"},
                    "members": [],
                    "owned_repos": [],
                },
            },
        )

    async def _rule_org_agent(
        _context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        nonlocal rule_org_calls
        rule_org_calls += 1
        return AgentResult(data={"id": "rule-org"})

    orchestrator = PipelineOrchestrator(
        context_gatherer=_context_gatherer,
        llm_organization_agent=_FailingLLMOrganizationRunner(),
        agent_runners={
            "org_agent": _rule_org_agent,
            "repo_agent": _rule_org_agent,
            "person_agent": _rule_org_agent,
            **_empty_class_agent_runners(),
        },
        retry_backoff_base=0,
        retry_max_retries=1,
    )
    plan = orchestrator.get_execution_plan("organization")

    with pytest.raises(RuntimeError, match="LLM organization runtime failed without fallback"):
        asyncio.run(
            orchestrator.execute(
                plan=plan,
                providers=_providers(),
                context={
                    "url_info": GitHubURLClassification(
                        normalized_url="https://github.com/orgs/example",
                        detected_type=GitHubURLType.ORGANIZATION,
                        owner="example",
                        repo=None,
                    ),
                    "source_url": "https://github.com/orgs/example",
                    "agent_runtime": "llm",
                },
            ),
        )

    assert rule_org_calls == 0
