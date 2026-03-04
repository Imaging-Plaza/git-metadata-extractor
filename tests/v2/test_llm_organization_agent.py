from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest
from jsonschema import validate

from src.v2.agents.llm.organization import LLMOrganizationAgentV2
from src.v2.agents.models import ProviderSet
from src.v2.llm.runtime import LLMRuntimeError, LLMRuntimeResult
from src.v2.providers.mock_github import MockGitHubProvider
from src.v2.providers.mock_infoscience import MockInfoscienceProvider
from src.v2.providers.mock_ror import MockRORProvider

_HAS_LLM_CREDENTIALS = bool(
    os.getenv("RCP_TOKEN") or os.getenv("OPENAI_API_KEY") or os.getenv("OPENROUTER_API_KEY")
)
llm_integration = pytest.mark.llm_integration
requires_llm_credentials = pytest.mark.skipif(
    not _HAS_LLM_CREDENTIALS,
    reason="No LLM provider credentials available (RCP_TOKEN / OPENAI_API_KEY / OPENROUTER_API_KEY)",
)

EXPECTED_PROMPT_TOKENS = 13
EXPECTED_COMPLETION_TOKENS = 29


class _FakeLLMRuntime:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    async def run_json_prompt(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        output_type: Any = None,
        tools: Any = None,
    ) -> LLMRuntimeResult:
        del output_type, tools
        assert system_prompt
        assert user_prompt
        return LLMRuntimeResult(
            payload=dict(self._payload),
            model="openai/gpt-test",
            provider="openai",
            tokens_prompt=EXPECTED_PROMPT_TOKENS,
            tokens_completion=EXPECTED_COMPLETION_TOKENS,
        )


def _providers() -> ProviderSet:
    return ProviderSet(github=MockGitHubProvider())


def _providers_full() -> ProviderSet:
    return ProviderSet(
        github=MockGitHubProvider(),
        ror=MockRORProvider(),
        infoscience=MockInfoscienceProvider(),
    )


def _valid_organization_payload() -> dict[str, Any]:
    return {
        "id": "github",
        "type": "org:Organization",
        "shacl": "pulse:OrganizationShape",
        "identifiers": {
            "pulse:ror": None,
            "pulse:infoscienceOrganizationIdentifier": None,
            "pulse:githubOrganizationHandle": "github",
            "uuid": "f887181e-9b8c-4c55-8ee6-d34285fdbed4",
        },
        "idSource": "pulse:githubOrganizationHandle",
        "schema:name": "GitHub",
        "schema:identifier": None,
        "pulse:githubOrganizationHandle": "github",
        "pulse:infoscienceOrganizationIdentifier": None,
        "pulse:OrganizationType": "pulse:PrivateCompany",
        "pulse:githubOrgFollowers": 123,
        "org:hasUnit": [],
        "org:unitOf": None,
        "pulse:owns": ["owner/repo"],
    }


def test_llm_organization_agent_validates_payload_and_exposes_model_metadata(
    load_schema,
) -> None:
    agent = LLMOrganizationAgentV2(
        llm_runtime=_FakeLLMRuntime(_valid_organization_payload()),
    )

    result = asyncio.run(
        agent.run({"org_name": "github"}, _providers()),
    )

    schema = load_schema("agent", "organization")
    validate(instance=result.data, schema=schema)

    assert result.model == "openai/gpt-test"
    assert result.provider == "openai"
    assert result.tokens_prompt == EXPECTED_PROMPT_TOKENS
    assert result.tokens_completion == EXPECTED_COMPLETION_TOKENS
    assert result.stats["agent_runtime"] == "llm"


def test_llm_organization_agent_propagates_llm_runtime_error() -> None:
    class _RaisingRuntime:
        async def run_json_prompt(
            self,
            *,
            system_prompt: str,
            user_prompt: str,
            output_type: Any = None,
            tools: Any = None,
        ) -> LLMRuntimeResult:
            del system_prompt, user_prompt, output_type, tools
            raise LLMRuntimeError("Schema validation failed after retries")

    agent = LLMOrganizationAgentV2(llm_runtime=_RaisingRuntime())

    with pytest.raises(LLMRuntimeError, match="Schema validation failed after retries"):
        asyncio.run(
            agent.run({"org_name": "github"}, _providers()),
        )


def test_llm_organization_agent_timeout_includes_identifier_and_timeout_seconds() -> None:
    class _SlowRuntime:
        async def run_json_prompt(
            self,
            *,
            system_prompt: str,
            user_prompt: str,
            output_type: Any = None,
            tools: Any = None,
        ) -> LLMRuntimeResult:
            del system_prompt, user_prompt, output_type, tools
            await asyncio.sleep(60)
            raise AssertionError("unreachable")

    agent = LLMOrganizationAgentV2(
        llm_runtime=_SlowRuntime(),
        llm_call_timeout_seconds=0.1,
    )

    with pytest.raises(LLMRuntimeError, match=r"github.*timed out after 0\.1s"):
        asyncio.run(
            agent.run({"org_name": "github"}, _providers()),
        )


def test_llm_organization_agent_records_strict_schema_warnings() -> None:
    payload = _valid_organization_payload()
    payload["identifiers"] = {
        "pulse:ror": None,
        "pulse:infoscienceOrganizationIdentifier": None,
        "pulse:githubOrganizationHandle": "github",
        "uuid": "not-a-valid-uuid",
    }

    agent = LLMOrganizationAgentV2(llm_runtime=_FakeLLMRuntime(payload))

    result = asyncio.run(
        agent.run({"org_name": "github"}, _providers()),
    )

    assert result.warnings, "Expected strict-schema warnings but got none"


def test_llm_organization_agent_builds_tools_from_providers() -> None:
    captured_tools: list[Any] = []

    class _CapturingRuntime:
        async def run_json_prompt(
            self,
            *,
            system_prompt: str,
            user_prompt: str,
            output_type: Any = None,
            tools: Any = None,
        ) -> LLMRuntimeResult:
            del system_prompt, user_prompt, output_type
            if tools:
                captured_tools.extend(tools)
            return LLMRuntimeResult(
                payload=_valid_organization_payload(),
                model="openai/gpt-test",
                provider="openai",
            )

    agent = LLMOrganizationAgentV2(llm_runtime=_CapturingRuntime())

    asyncio.run(
        agent.run({"org_name": "github"}, _providers_full()),
    )

    tool_names = [getattr(tool, "name", None) for tool in captured_tools]
    assert "search_ror_organizations" in tool_names
    assert "search_infoscience_orgunit" in tool_names


def test_llm_organization_agent_appends_runtime_prompt_context_blocks() -> None:
    captured_user_prompts: list[str] = []

    class _CapturingRuntime:
        async def run_json_prompt(
            self,
            *,
            system_prompt: str,
            user_prompt: str,
            output_type: Any = None,
            tools: Any = None,
        ) -> LLMRuntimeResult:
            del system_prompt, output_type, tools
            captured_user_prompts.append(user_prompt)
            return LLMRuntimeResult(
                payload=_valid_organization_payload(),
                model="openai/gpt-test",
                provider="openai",
            )

    agent = LLMOrganizationAgentV2(llm_runtime=_CapturingRuntime())
    upstream_json = '{"repo_agent":{"id":"repo-root"}}'
    prompt_appendix = "README_FRAGMENT_1\nREADME_FRAGMENT_2"

    asyncio.run(
        agent.run(
            {
                "org_name": "github",
                "upstream_stage_outputs_json": upstream_json,
                "user_prompt_appendix": prompt_appendix,
            },
            _providers(),
        ),
    )

    assert len(captured_user_prompts) == 1
    prompt = captured_user_prompts[0]
    assert "## Upstream Stage Outputs (JSON)" in prompt
    assert upstream_json in prompt
    assert "## Additional Context (verbatim text)" in prompt
    assert prompt_appendix in prompt


def test_llm_organization_agent_no_tools_without_providers() -> None:
    captured_tools: list[Any] = []

    class _CapturingRuntime:
        async def run_json_prompt(
            self,
            *,
            system_prompt: str,
            user_prompt: str,
            output_type: Any = None,
            tools: Any = None,
        ) -> LLMRuntimeResult:
            del system_prompt, user_prompt, output_type
            captured_tools.extend(tools or [])
            return LLMRuntimeResult(
                payload=_valid_organization_payload(),
                model="openai/gpt-test",
                provider="openai",
            )

    agent = LLMOrganizationAgentV2(llm_runtime=_CapturingRuntime())

    asyncio.run(
        agent.run({"org_name": "github"}, _providers()),
    )

    assert captured_tools == []


def test_llm_organization_agent_works_with_minimal_context() -> None:
    agent = LLMOrganizationAgentV2(
        llm_runtime=_FakeLLMRuntime(_valid_organization_payload()),
    )

    result = asyncio.run(
        agent.run({"org_name": "github"}, _providers()),
    )

    assert result.data["id"] == "github"
    assert result.data["type"] == "org:Organization"
    assert result.stats["agent_runtime"] == "llm"


def test_llm_organization_agent_raises_on_empty_context() -> None:
    agent = LLMOrganizationAgentV2(
        llm_runtime=_FakeLLMRuntime(_valid_organization_payload()),
    )

    with pytest.raises(ValueError, match="missing a GitHub organization handle"):
        asyncio.run(
            agent.run({}, _providers()),
        )


@llm_integration
@requires_llm_credentials
def test_llm_organization_agent_real_provider_call() -> None:
    agent = LLMOrganizationAgentV2()
    context = {
        "org_name": "github",
        "source_repositories": ["octocat/Hello-World"],
    }

    result = asyncio.run(agent.run(context, _providers_full()))

    assert result.data["type"] == "org:Organization"
    assert result.data["shacl"] == "pulse:OrganizationShape"
    assert result.model is not None
    assert result.provider is not None
    assert result.stats["agent_runtime"] == "llm"

    import json

    print("\n--- LLM Organization Agent Result ---")
    print(f"Model:    {result.model}")
    print(f"Provider: {result.provider}")
    print(f"Tokens:   prompt={result.tokens_prompt}, completion={result.tokens_completion}")
    print(f"Warnings: {result.warnings}")
    print(json.dumps(result.data, indent=2, ensure_ascii=False))
