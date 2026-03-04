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
    class _FakeRepositoryAgent:
        async def run(self, context: dict[str, Any], providers: ProviderSet) -> AgentResult:
            del context, providers
            return AgentResult(
                data={
                    "id": "repo-id",
                    "type": "schema:SoftwareSourceCode",
                    "shacl": "pulse:RepositoryShape",
                },
                model="openai/gpt-test",
                provider="openai",
                tokens_prompt=10,
                tokens_completion=5,
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
                        "id": "ok-user",
                        "type": "schema:Person",
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

    asyncio.run(
        run_script._run(
            "octo/repo",
            person_timeout_seconds=180.0,
            organization_timeout_seconds=180.0,
            max_concurrency=3,
            heartbeat_seconds=0.01,
        ),
    )

    captured = capsys.readouterr()
    stdout = captured.out

    assert "heartbeat:" in stdout
    assert "Person summary: ok=1 timeout=1 error=1" in stdout
    assert "Organization summary: ok=2 timeout=1 error=1" in stdout
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

    assert _FakeOrganizationAgent.captured_contexts
    org_context = _FakeOrganizationAgent.captured_contexts[0]
    assert "repository_context" in org_context
    assert org_context["source_repositories"] == ["octo/repo"]

    upstream_json = org_context.get("upstream_stage_outputs_json")
    assert isinstance(upstream_json, str)
    upstream_payload = json.loads(upstream_json)
    assert "repo_agent" in upstream_payload
    assert "person_agent:ok-user" in upstream_payload


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
            "id": "urn:git-metadata-extractor:entity:0000-0001-2345-6789",
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
        "urn:git-metadata-extractor:entity:0000-0001-2345-6789",
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
