from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import scripts.v2.run_llm_repo_persons_and_orgs as run_script
from src.v2.agents.models import AgentResult, ProviderSet


def test_run_llm_repo_persons_and_orgs_handles_partial_failures_and_prints_jsonld(
    monkeypatch,
    capsys,
) -> None:
    person_ok_id = "urn:pulse:person-ok-user"
    person_ok2_id = "urn:pulse:person-ok-user-2"

    class _FakeRepositoryAgent:
        async def run(self, context: dict[str, Any], providers: ProviderSet) -> AgentResult:
            del context, providers
            return AgentResult(
                data={
                    "id": "octo/repo",
                    "type": "schema:SoftwareSourceCode",
                    "shacl": "pulse:RepositoryShape",
                    "schema:author": ["ok-user", "ghost-author"],
                },
                model="openai/gpt-test",
                provider="openai",
                tokens_prompt=10,
                tokens_completion=5,
                stats={
                    "derivation": {
                        "repository_full_name": "octo/repo",
                        "contributors": [
                            {"login": "ok-user", "contributions": 5},
                            {"login": "ok-user-2", "contributions": 2},
                        ],
                    },
                },
            )

    class _FakePersonAgent:
        def __init__(
            self,
            *,
            llm_runtime: Any | None = None,
            llm_call_timeout_seconds: float = 180.0,
        ) -> None:
            del llm_runtime
            self._timeout = llm_call_timeout_seconds

        async def run(self, context: dict[str, Any], providers: ProviderSet) -> AgentResult:
            del providers
            username = context["username"]
            if username == "ok-user":
                return AgentResult(
                    data={
                        "id": person_ok_id,
                        "type": "schema:Person",
                        "pulse:githubUsername": "ok-user",
                        "org:hasMembership": [
                            "ok-user_org-owner",
                            "ok-user_org-timeout",
                            "ok-user_org-error",
                            "ok-user_org-success",
                        ],
                    },
                    model="openai/gpt-test",
                    provider="openai",
                    tokens_prompt=20,
                    tokens_completion=8,
                    stats={
                        "derivation": {
                            "person_id": person_ok_id,
                            "affiliation_names": ["org-owner"],
                            "orcid_affiliations": [
                                {
                                    "organization": "org-owner",
                                    "role": "Research Engineer",
                                    "start_date": "2020-01-01",
                                    "end_date": None,
                                },
                            ],
                        },
                    },
                )
            if username == "ok-user-2":
                return AgentResult(
                    data={
                        "id": person_ok2_id,
                        "type": "schema:Person",
                        "pulse:githubUsername": "ok-user-2",
                        "org:hasMembership": [
                            "ok-user-2_org-success",
                        ],
                    },
                    model="openai/gpt-test",
                    provider="openai",
                    tokens_prompt=19,
                    tokens_completion=7,
                    stats={
                        "derivation": {
                            "person_id": person_ok2_id,
                            "affiliation_names": ["org-success"],
                            "orcid_affiliations": [],
                        },
                    },
                )
            if username == "timeout-user":
                await asyncio.sleep(0.05)
                raise RuntimeError(
                    f"{username} — LLM call timed out after {self._timeout:.1f}s",
                )
            raise RuntimeError(f"{username} exploded")

    class _FakeOrganizationAgent:
        captured_contexts: list[dict[str, Any]] = []

        def __init__(
            self,
            *,
            llm_runtime: Any | None = None,
            llm_call_timeout_seconds: float = 180.0,
        ) -> None:
            del llm_runtime
            self._timeout = llm_call_timeout_seconds

        async def run(self, context: dict[str, Any], providers: ProviderSet) -> AgentResult:
            del providers
            _FakeOrganizationAgent.captured_contexts.append(dict(context))
            org_name = context["org_name"]
            if org_name in {"org-owner", "org-success"}:
                return AgentResult(
                    data={
                        "id": org_name,
                        "type": "org:Organization",
                        "schema:name": org_name,
                    },
                    model="openai/gpt-test",
                    provider="openai",
                    tokens_prompt=18,
                    tokens_completion=7,
                )
            if org_name == "org-timeout":
                await asyncio.sleep(0.05)
                raise RuntimeError(
                    f"{org_name} — LLM call timed out after {self._timeout:.1f}s",
                )
            raise RuntimeError(f"{org_name} exploded")

    class _FakeArticleAgent:
        captured_contexts: list[dict[str, Any]] = []

        def __init__(
            self,
            *,
            llm_runtime: Any | None = None,
            llm_call_timeout_seconds: float = 180.0,
        ) -> None:
            del llm_runtime
            self._timeout = llm_call_timeout_seconds

        async def run(self, context: dict[str, Any], providers: ProviderSet) -> AgentResult:
            del providers
            _FakeArticleAgent.captured_contexts.append(dict(context))
            return AgentResult(
                data={
                    "id": "10.1000/repo-article",
                    "type": "schema:ScholarlyArticle",
                    "schema:author": [person_ok_id],
                },
                stats={
                    "articles": [
                        {
                            "id": "10.1000/repo-article-2",
                            "type": "schema:ScholarlyArticle",
                            "schema:author": [person_ok2_id],
                        },
                    ],
                },
            )

    class _FakeMembershipAgent:
        captured_contexts: list[dict[str, Any]] = []

        def __init__(
            self,
            *,
            llm_runtime: Any | None = None,
            llm_call_timeout_seconds: float = 180.0,
        ) -> None:
            del llm_runtime
            self._timeout = llm_call_timeout_seconds

        async def run(self, context: dict[str, Any], providers: ProviderSet) -> AgentResult:
            del providers
            _FakeMembershipAgent.captured_contexts.append(dict(context))
            seed = context["membership_seed"]
            if seed == person_ok_id:
                await asyncio.sleep(0.05)
                raise RuntimeError(
                    f"{seed} — LLM call timed out after {self._timeout:.1f}s",
                )
            return AgentResult(
                data={
                    "id": f"{seed}_org-success",
                    "type": "org:Membership",
                    "org:organization": "org-success",
                },
                stats={"memberships": []},
            )

    class _FakeContributionAgent:
        captured_contexts: list[dict[str, Any]] = []

        def __init__(
            self,
            *,
            llm_runtime: Any | None = None,
            llm_call_timeout_seconds: float = 180.0,
        ) -> None:
            del llm_runtime
            self._timeout = llm_call_timeout_seconds

        async def run(self, context: dict[str, Any], providers: ProviderSet) -> AgentResult:
            del providers
            _FakeContributionAgent.captured_contexts.append(dict(context))
            return AgentResult(
                data={
                    "id": f"{person_ok_id}_octo/repo",
                    "type": "pulse:Contribution",
                    "schema:author": person_ok_id,
                    "pulse:contributionTo": "octo/repo",
                },
                stats={
                    "contributions": [
                        {
                            "id": f"{person_ok2_id}_octo/repo",
                            "type": "pulse:Contribution",
                            "schema:author": person_ok2_id,
                            "pulse:contributionTo": "octo/repo",
                        },
                    ],
                },
            )

    async def _fake_gather_context(
        _detected_type: str,
        _url_info: Any,
        _providers: ProviderSet,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            context={
                "repository": {
                    "metadata": {
                        "full_name": "octo/repo",
                        "owner": {"login": "org-owner", "type": "Organization"},
                    },
                    "contributors": [
                        {"login": "ok-user"},
                        {"login": "ok-user-2"},
                        {"login": "timeout-user"},
                        {"login": "error-user"},
                    ],
                    "languages": {"Python": 1},
                    "readme_content": "README",
                },
            },
            warnings=[],
        )

    monkeypatch.setattr(
        run_script,
        "classify_github_url",
        lambda _repo: SimpleNamespace(
            owner="octo",
            repo="repo",
            normalized_url="https://github.com/octo/repo",
        ),
    )
    monkeypatch.setattr(
        run_script,
        "_default_provider_set",
        lambda *, use_mock_providers: ProviderSet(github=object()),
    )
    monkeypatch.setattr(run_script, "gather_context", _fake_gather_context)
    monkeypatch.setattr(run_script, "LLMRepositoryAgentV2", _FakeRepositoryAgent)
    monkeypatch.setattr(run_script, "LLMPersonAgentV2", _FakePersonAgent)
    monkeypatch.setattr(run_script, "LLMOrganizationAgentV2", _FakeOrganizationAgent)
    monkeypatch.setattr(run_script, "LLMArticleAgentV2", _FakeArticleAgent)
    monkeypatch.setattr(run_script, "LLMMembershipAgentV2", _FakeMembershipAgent)
    monkeypatch.setattr(run_script, "LLMContributionAgentV2", _FakeContributionAgent)

    asyncio.run(
        run_script._run(
            "octo/repo",
            person_timeout_seconds=180.0,
            organization_timeout_seconds=180.0,
            article_timeout_seconds=180.0,
            membership_timeout_seconds=180.0,
            contribution_timeout_seconds=180.0,
            max_concurrency=3,
            heartbeat_seconds=0.01,
        ),
    )

    captured = capsys.readouterr()
    stdout = captured.out

    stage_markers = [
        "[ 1 / 7 ]  Gathering GIMIE context",
        "[ 2 / 7 ]  Running LLM repository agent",
        "[ 3 / 7 ]  Running LLM person agent",
        "[ 4 / 7 ]  Running LLM organization agent",
        "[ 5 / 7 ]  Running LLM article agent",
        "[ 6 / 7 ]  Running LLM membership agent",
        "[ 7 / 7 ]  Running LLM contribution agent",
    ]
    stage_positions = [stdout.index(marker) for marker in stage_markers]
    assert stage_positions == sorted(stage_positions)

    assert "heartbeat:" in stdout
    assert "Person summary: ok=2 timeout=1 error=1" in stdout
    assert "Organization summary: ok=2 timeout=1 error=1" in stdout
    assert "Article summary: ok=2 timeout=0 error=0" in stdout
    assert "Membership summary: ok=1 timeout=1 error=0" in stdout
    assert "Contribution summary: ok=2 timeout=0 error=0" in stdout
    assert "Raw combined JSON-LD (pre-reconciliation):" in stdout

    jsonld_section = stdout.split(
        "Raw combined JSON-LD (pre-reconciliation):\n",
        maxsplit=1,
    )[1]
    jsonld_text = jsonld_section.split(f"\n{run_script._SEP}", maxsplit=1)[0]
    jsonld_payload = json.loads(jsonld_text)
    assert "@graph" in jsonld_payload
    graph_nodes = jsonld_payload["@graph"]
    assert any(node.get("@type") == "schema:SoftwareSourceCode" for node in graph_nodes)
    assert any(node.get("@type") == "schema:Person" for node in graph_nodes)
    assert any(node.get("@type") == "org:Organization" for node in graph_nodes)
    assert any(node.get("@type") == "schema:ScholarlyArticle" for node in graph_nodes)
    assert any(node.get("@type") == "org:Membership" for node in graph_nodes)
    assert any(node.get("@type") == "pulse:Contribution" for node in graph_nodes)

    repository_node = next(
        node
        for node in graph_nodes
        if node.get("@type") == "schema:SoftwareSourceCode"
    )
    assert {"@id": person_ok_id} in repository_node.get("schema:author", [])

    assert _FakeOrganizationAgent.captured_contexts
    org_context = _FakeOrganizationAgent.captured_contexts[0]
    assert "repository_context" in org_context
    assert org_context["source_repositories"] == ["octo/repo"]

    upstream_json = org_context.get("upstream_stage_outputs_json")
    assert isinstance(upstream_json, str)
    upstream_payload = json.loads(upstream_json)
    assert "repo_agent" in upstream_payload
    assert "person_agent:ok-user" in upstream_payload
    assert "person_agent:ok-user-2" in upstream_payload

    assert _FakeArticleAgent.captured_contexts
    article_context = _FakeArticleAgent.captured_contexts[0]
    assert isinstance(article_context.get("known_persons"), list)
    assert isinstance(article_context.get("known_organizations"), list)
    assert isinstance(article_context.get("known_repositories"), list)
    assert isinstance(article_context.get("pipeline_outputs"), dict)
    assert "repository_context" in article_context

    assert _FakeMembershipAgent.captured_contexts
    membership_context = _FakeMembershipAgent.captured_contexts[0]
    assert membership_context["known_persons"][0]["affiliations"] == ["org-owner"]
    assert isinstance(membership_context.get("known_organizations"), list)
    assert isinstance(membership_context.get("pipeline_outputs"), dict)

    assert _FakeContributionAgent.captured_contexts
    contribution_context = _FakeContributionAgent.captured_contexts[0]
    assert contribution_context["known_repositories"][0]["contributors"] == [
        {"login": "ok-user", "contributions": 5},
        {"login": "ok-user-2", "contributions": 2},
    ]
    assert isinstance(contribution_context.get("known_persons"), list)


def test_normalize_entities_for_debug_jsonld_resolves_authors_merges_orgs_and_strips_nulls() -> None:
    entities = [
        {
            "id": "owner/repo",
            "type": "schema:SoftwareSourceCode",
            "schema:name": "owner/repo",
            "schema:author": ["alice", "ghost"],
            "pulse:githubRepositoryHandle": "owner/repo",
            "pulse:ownedBy": None,
        },
        {
            "id": "urn:pulse:0000-0001-2345-6789",
            "type": "schema:Person",
            "schema:name": "Alice",
            "pulse:githubUsername": "alice",
            "pulse:owns": ["owner/repo"],
        },
        {
            "id": "https://ror.org/019whta54",
            "type": "org:Organization",
            "schema:name": "University of Lausanne",
            "aliases": ["Université de Lausanne"],
            "identifiers": {
                "pulse:ror": "https://ror.org/019whta54",
            },
            "schema:identifier": None,
            "pulse:owns": ["owner/repo"],
        },
        {
            "id": "https://ror.org/019whta54",
            "type": "org:Organization",
            "schema:name": "University of Lausanne",
            "aliases": ["Universite de Lausanne"],
            "org:hasUnit": ["https://ror.org/03kwyfa97"],
        },
    ]

    normalized = run_script._normalize_entities_for_debug_jsonld(entities)

    repository = next(
        entity
        for entity in normalized
        if entity.get("type") == "schema:SoftwareSourceCode"
    )
    assert repository["schema:author"] == [
        "urn:pulse:0000-0001-2345-6789",
        "ghost",
    ]
    assert "pulse:ownedBy" not in repository

    person = next(
        entity
        for entity in normalized
        if entity.get("type") == "schema:Person"
    )
    assert person["pulse:owns"] == ["owner/repo"]

    organizations = [
        entity
        for entity in normalized
        if entity.get("type") == "org:Organization"
    ]
    assert len(organizations) == 1
    assert organizations[0]["schema:identifier"] == "https://ror.org/019whta54"
    assert organizations[0]["pulse:owns"] == ["owner/repo"]
    assert sorted(organizations[0]["aliases"]) == [
        "Universite de Lausanne",
        "Université de Lausanne",
    ]


def test_run_llm_repo_persons_and_orgs_can_verify_links_with_independent_agent(
    monkeypatch,
    capsys,
) -> None:
    class _FakeRepositoryAgent:
        async def run(self, context: dict[str, Any], providers: ProviderSet) -> AgentResult:
            del context, providers
            return AgentResult(
                data={
                    "id": "octo/repo",
                    "type": "schema:SoftwareSourceCode",
                    "shacl": "pulse:RepositoryShape",
                    "schema:name": "octo/repo",
                    "schema:author": ["ok-user"],
                    "schema:license": "https://spdx.org/licenses/MIT.html",
                    "pulse:githubRepositoryHandle": "octo/repo",
                },
                model="openai/gpt-test",
                provider="openai",
            )

    class _FakePersonAgent:
        def __init__(
            self,
            *,
            llm_runtime: Any | None = None,
            llm_call_timeout_seconds: float = 180.0,
        ) -> None:
            del llm_runtime, llm_call_timeout_seconds

        async def run(self, context: dict[str, Any], providers: ProviderSet) -> AgentResult:
            del providers
            username = context["username"]
            return AgentResult(
                data={
                    "id": username,
                    "type": "schema:Person",
                    "schema:name": username,
                    "pulse:githubUsername": username,
                    "schema:url": "https://github.com/ok-user",
                },
                model="openai/gpt-test",
                provider="openai",
            )

    class _FakeOrganizationAgent:
        def __init__(
            self,
            *,
            llm_runtime: Any | None = None,
            llm_call_timeout_seconds: float = 180.0,
        ) -> None:
            del llm_runtime, llm_call_timeout_seconds

        async def run(self, context: dict[str, Any], providers: ProviderSet) -> AgentResult:
            del providers
            return AgentResult(
                data={
                    "id": context["org_name"],
                    "type": "org:Organization",
                    "schema:name": context["org_name"],
                    "schema:identifier": "https://ror.org/02s376052",
                },
                model="openai/gpt-test",
                provider="openai",
            )

    class _NoopClassAgent:
        def __init__(
            self,
            *,
            llm_runtime: Any | None = None,
            llm_call_timeout_seconds: float = 180.0,
        ) -> None:
            del llm_runtime, llm_call_timeout_seconds

        async def run(self, context: dict[str, Any], providers: ProviderSet) -> AgentResult:
            del context, providers
            return AgentResult(data={}, model="openai/gpt-test", provider="openai")

    class _FakeLinkVeracityAgent:
        captured_contexts: list[dict[str, Any]] = []

        def __init__(
            self,
            *,
            llm_runtime: Any | None = None,
            llm_call_timeout_seconds: float = 180.0,
        ) -> None:
            del llm_runtime, llm_call_timeout_seconds

        async def run(self, context: dict[str, Any], providers: ProviderSet) -> AgentResult:
            del providers
            _FakeLinkVeracityAgent.captured_contexts.append(dict(context))
            link = context["link"]
            return AgentResult(
                data={
                    "link": link,
                    "relationship_supported": link.startswith("https://"),
                    "relationship_summary": "verified",
                    "fetched_successfully": True,
                },
                model="openai/gpt-test",
                provider="openai",
            )

    async def _fake_gather_context(
        _detected_type: str,
        _url_info: Any,
        _providers: ProviderSet,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            context={
                "repository": {
                    "metadata": {
                        "full_name": "octo/repo",
                        "owner": {"login": "org-owner", "type": "Organization"},
                    },
                    "contributors": [{"login": "ok-user"}],
                    "languages": {"Python": 1},
                    "readme_content": "README",
                },
            },
            warnings=[],
        )

    monkeypatch.setattr(
        run_script,
        "classify_github_url",
        lambda _repo: SimpleNamespace(
            owner="octo",
            repo="repo",
            normalized_url="https://github.com/octo/repo",
        ),
    )
    monkeypatch.setattr(
        run_script,
        "_default_provider_set",
        lambda *, use_mock_providers: ProviderSet(github=object()),
    )
    monkeypatch.setattr(run_script, "gather_context", _fake_gather_context)
    monkeypatch.setattr(run_script, "LLMRepositoryAgentV2", _FakeRepositoryAgent)
    monkeypatch.setattr(run_script, "LLMPersonAgentV2", _FakePersonAgent)
    monkeypatch.setattr(run_script, "LLMOrganizationAgentV2", _FakeOrganizationAgent)
    monkeypatch.setattr(run_script, "LLMArticleAgentV2", _NoopClassAgent)
    monkeypatch.setattr(run_script, "LLMMembershipAgentV2", _NoopClassAgent)
    monkeypatch.setattr(run_script, "LLMContributionAgentV2", _NoopClassAgent)
    monkeypatch.setattr(run_script, "LLMLinkVeracityAgentV2", _FakeLinkVeracityAgent)

    asyncio.run(
        run_script._run(
            "octo/repo",
            verify_links=True,
            link_verification_timeout_seconds=60.0,
            link_verification_max_concurrency=2,
            heartbeat_seconds=0.01,
        ),
    )

    stdout = capsys.readouterr().out
    assert "[ links ]  Verifying unique http(s) links via LLM + Selenium tool" in stdout
    assert "Link verification summary: yes=" in stdout

    links = sorted(
        {
            context["link"]
            for context in _FakeLinkVeracityAgent.captured_contexts
            if isinstance(context.get("link"), str)
        },
    )
    assert "https://github.com/ok-user" in links
    assert "https://ror.org/02s376052" in links
    assert "https://spdx.org/licenses/MIT.html" in links
