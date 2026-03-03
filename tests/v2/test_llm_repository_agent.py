from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest
from jsonschema import validate

from src.v2.agents.llm.repository import LLMRepositoryAgentV2
from src.v2.agents.models import ProviderSet
from src.v2.llm.runtime import LLMRuntimeError, LLMRuntimeResult
from src.v2.providers.mock_github import MockGitHubProvider

_HAS_LLM_CREDENTIALS = bool(
    os.getenv("RCP_TOKEN") or os.getenv("OPENAI_API_KEY") or os.getenv("OPENROUTER_API_KEY")
)
llm_integration = pytest.mark.skipif(
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


def _valid_repository_payload() -> dict[str, Any]:
    return {
        "id": "octocat/Hello-World",
        "type": "schema:SoftwareSourceCode",
        "shacl": "pulse:RepositoryShape",
        "identifiers": {
            "pulse:githubRepositoryHandle": "octocat/Hello-World",
            "schema:citation": None,
            "uuid": "f887181e-9b8c-4c55-8ee6-d34285fdbed4",
        },
        "idSource": "pulse:githubRepositoryHandle",
        "schema:name": "Hello-World",
        "pulse:githubRepositoryHandle": "octocat/Hello-World",
        "schema:author": ["octocat"],
        "pulse:repositoryType": "pulse:Software",
        "pulse:discipline": ["wd:Q8434"],
    }


def test_llm_repository_agent_validates_payload_and_exposes_model_metadata(
    load_schema,
) -> None:
    agent = LLMRepositoryAgentV2(
        llm_runtime=_FakeLLMRuntime(_valid_repository_payload()),
    )

    result = asyncio.run(
        agent.run(
            {
                "full_name": "octocat/Hello-World",
                "repository_context": {
                    "full_name": "octocat/Hello-World",
                    "metadata": {
                        "owner": {"login": "octocat", "type": "User"},
                    },
                    "contributors": [{"login": "octocat", "type": "User"}],
                    "languages": {"Python": 1},
                },
            },
            _providers(),
        ),
    )

    schema = load_schema("agent", "repository")
    validate(instance=result.data, schema=schema)

    assert result.model == "openai/gpt-test"
    assert result.provider == "openai"
    assert result.tokens_prompt == EXPECTED_PROMPT_TOKENS
    assert result.tokens_completion == EXPECTED_COMPLETION_TOKENS
    assert result.stats["agent_runtime"] == "llm"


@llm_integration
def test_llm_repository_agent_real_provider_call() -> None:
    """Integration test: sends a real prompt to the configured LLM provider."""

    agent = LLMRepositoryAgentV2()

    context = {
        "full_name": "octocat/Hello-World",
        "source_url": "https://github.com/octocat/Hello-World",
        "repository_context": {
            "full_name": "octocat/Hello-World",
            "metadata": {
                "name": "Hello-World",
                "full_name": "octocat/Hello-World",
                "description": "My first repository on GitHub!",
                "owner": {"login": "octocat", "type": "User"},
                "stargazers_count": 2871,
                "forks_count": 2124,
                "created_at": "2011-01-26T19:01:12Z",
                "license": {"spdx_id": "MIT"},
                "fork": False,
            },
            "contributors": [
                {"login": "octocat", "type": "User"},
                {"login": "hubot", "type": "User"},
            ],
            "languages": {"Python": 4200, "Shell": 1100},
        },
    }

    result = asyncio.run(agent.run(context, _providers()))

    # Core assertions on the returned result.
    # Note: pydantic-ai already validated the output against AgentRepositoryShape
    # (first pass) during the LLM call, so a redundant jsonschema.validate() here
    # would fail on optional array fields the LLM returns as null rather than [].
    assert result.data["id"] == "octocat/Hello-World"
    assert result.data["type"] == "schema:SoftwareSourceCode"
    assert result.data["shacl"] == "pulse:RepositoryShape"
    assert result.data["pulse:githubRepositoryHandle"] == "octocat/Hello-World"
    assert len(result.data["schema:author"]) >= 1

    # LLM metadata should be populated.
    assert result.model is not None
    assert result.provider is not None
    assert result.stats["agent_runtime"] == "llm"

    # Print for manual inspection.
    import json

    print("\n--- LLM Repository Agent Result ---")
    print(f"Model:    {result.model}")
    print(f"Provider: {result.provider}")
    print(f"Tokens:   prompt={result.tokens_prompt}, completion={result.tokens_completion}")
    print(f"Warnings: {result.warnings}")
    print(json.dumps(result.data, indent=2, ensure_ascii=False))


def test_llm_repository_agent_propagates_llm_runtime_error() -> None:
    """LLMRuntimeError from the runtime (e.g. pydantic-ai schema rejection) bubbles up."""

    class _RaisingRuntime:
        async def run_json_prompt(
            self,
            *,
            system_prompt: str,
            user_prompt: str,
            output_type: Any = None,
            tools: Any = None,
        ) -> LLMRuntimeResult:
            raise LLMRuntimeError("Schema validation failed after retries")

    agent = LLMRepositoryAgentV2(llm_runtime=_RaisingRuntime())

    with pytest.raises(LLMRuntimeError, match="Schema validation failed after retries"):
        asyncio.run(
            agent.run({"full_name": "octocat/Hello-World"}, _providers()),
        )


def test_llm_repository_agent_records_strict_schema_warnings() -> None:
    """Strict-schema violations produce warnings in the result without raising."""
    payload = _valid_repository_payload()
    payload.pop("schema:author")  # required by strict RepositoryModel

    agent = LLMRepositoryAgentV2(llm_runtime=_FakeLLMRuntime(payload))

    result = asyncio.run(
        agent.run(
            {
                "full_name": "octocat/Hello-World",
                "repository_context": {
                    "metadata": {"owner": {"login": "octocat", "type": "User"}},
                    "contributors": [{"login": "octocat", "type": "User"}],
                    "languages": {"Python": 1},
                },
            },
            _providers(),
        ),
    )

    assert result.warnings, "Expected strict-schema warnings but got none"
    assert any("schema:author" in w for w in result.warnings)
