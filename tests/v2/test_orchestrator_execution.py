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
    assert "contribution_agent:repo-root" in result.agent_results
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
                    "metadata": {"owner": {"login": "sdsc-ordes", "type": "Organization"}},
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
    assert [context["contribution_seed"] for context in captured_contribution_contexts] == [
        "repo-root",
    ]

    membership_context = captured_membership_contexts[0]
    assert membership_context["known_persons"][0]["affiliations"] == ["EPFL"]
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
    assert captured_contribution_seeds == [
        "octocat/repo-a",
        "octocat/repo-b",
        "github/repo-a",
    ]


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
    assert result.agent_results["contribution_agent:repo-root"].is_partial is False
    assert attempt_count["bob"] == EXPECTED_BOB_RETRY_ATTEMPTS
