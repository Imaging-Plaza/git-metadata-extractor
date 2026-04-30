from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest
from jsonschema import validate

from src.v2.agents.llm.person import LLMPersonAgentV2
from src.v2.agents.models import ProviderSet
from src.v2.agents.llm.runtime import LLMRuntimeError, LLMRuntimeResult
from src.v2.ingest.providers.mock_github import MockGitHubProvider
from src.v2.ingest.providers.mock_infoscience import MockInfoscienceProvider
from src.v2.ingest.providers.mock_orcid import MockORCIDProvider

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
        orcid=MockORCIDProvider(),
        infoscience=MockInfoscienceProvider(),
    )


def _valid_person_payload() -> dict[str, Any]:
    return {
        "id": "octocat",
        "type": "schema:Person",
        "shacl": "pulse:PersonShape",
        "identifiers": {
            "pulse:orcid": None,
            "pulse:infosciencePersonIdentifier": None,
            "pulse:githubUsername": "octocat",
            "uuid": "f887181e-9b8c-4c55-8ee6-d34285fdbed4",
        },
        "idSource": "pulse:githubUsername",
        "schema:name": "The Octocat",
        "pulse:githubUsername": "octocat",
    }


def test_llm_person_agent_validates_payload_and_exposes_model_metadata(
    load_schema,
) -> None:
    agent = LLMPersonAgentV2(
        llm_runtime=_FakeLLMRuntime(_valid_person_payload()),
    )

    result = asyncio.run(
        agent.run(
            {"username": "octocat"},
            _providers(),
        ),
    )

    schema = load_schema("agent", "person")
    validate(instance=result.data, schema=schema)

    assert result.model == "openai/gpt-test"
    assert result.provider == "openai"
    assert result.tokens_prompt == EXPECTED_PROMPT_TOKENS
    assert result.tokens_completion == EXPECTED_COMPLETION_TOKENS
    assert result.stats["agent_runtime"] == "llm"


def test_llm_person_agent_propagates_llm_runtime_error() -> None:
    """LLMRuntimeError from the runtime bubbles up unchanged."""

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

    agent = LLMPersonAgentV2(llm_runtime=_RaisingRuntime())

    with pytest.raises(LLMRuntimeError, match="Schema validation failed after retries"):
        asyncio.run(
            agent.run({"username": "octocat"}, _providers()),
        )


def test_llm_person_agent_timeout_includes_identifier_and_timeout_seconds() -> None:
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

    agent = LLMPersonAgentV2(
        llm_runtime=_SlowRuntime(),
        llm_call_timeout_seconds=0.1,
    )

    with pytest.raises(LLMRuntimeError, match=r"octocat.*timed out after 0\.1s"):
        asyncio.run(
            agent.run({"username": "octocat"}, _providers()),
        )


def test_llm_person_agent_records_strict_schema_warnings() -> None:
    """Strict-schema violations produce warnings without raising."""
    payload = _valid_person_payload()
    payload["idSource"] = "not-a-valid-id-source"

    agent = LLMPersonAgentV2(llm_runtime=_FakeLLMRuntime(payload))

    result = asyncio.run(
        agent.run({"username": "octocat"}, _providers()),
    )

    assert result.warnings, "Expected strict-schema warnings but got none"


def test_llm_person_agent_builds_tools_from_providers() -> None:
    """When infoscience and orcid providers are present, both tools are passed to the runtime."""

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
            if tools:
                captured_tools.extend(tools)
            return LLMRuntimeResult(
                payload=_valid_person_payload(),
                model="openai/gpt-test",
                provider="openai",
            )

    agent = LLMPersonAgentV2(llm_runtime=_CapturingRuntime())

    asyncio.run(
        agent.run(
            {"username": "octocat"},
            _providers_full(),
        ),
    )

    tool_names = [getattr(tool, "name", None) for tool in captured_tools]
    assert "fetch_link_content_via_selenium" in tool_names
    assert "hash_user_email" in tool_names
    assert "search_infoscience_person" in tool_names
    assert "get_orcid_record" in tool_names


def test_llm_person_agent_appends_runtime_prompt_context_blocks() -> None:
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
                payload=_valid_person_payload(),
                model="openai/gpt-test",
                provider="openai",
            )

    agent = LLMPersonAgentV2(llm_runtime=_CapturingRuntime())
    upstream_json = '{"repo_agent":{"id":"repo-root"}}'
    prompt_appendix = "README_FRAGMENT_1\nREADME_FRAGMENT_2"

    asyncio.run(
        agent.run(
            {
                "username": "octocat",
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


def test_llm_person_agent_exposes_selenium_tool_without_optional_providers() -> None:
    """When only GitHub provider is present, Selenium fetch tool remains available."""

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
            captured_tools.extend(tools or [])
            return LLMRuntimeResult(
                payload=_valid_person_payload(),
                model="openai/gpt-test",
                provider="openai",
            )

    agent = LLMPersonAgentV2(llm_runtime=_CapturingRuntime())

    asyncio.run(
        agent.run(
            {"username": "octocat"},
            _providers(),
        ),
    )

    tool_names = [getattr(tool, "name", None) for tool in captured_tools]
    assert tool_names == ["fetch_link_content_via_selenium", "hash_user_email"]


def test_llm_person_agent_works_with_orcid_only_context() -> None:
    """Agent succeeds when context has only an ORCID — no GitHub username required."""

    agent = LLMPersonAgentV2(
        llm_runtime=_FakeLLMRuntime(
            {
                "id": "0000-0002-1825-0097",
                "type": "schema:Person",
                "shacl": "pulse:PersonShape",
                "identifiers": {
                    "pulse:orcid": "0000-0002-1825-0097",
                    "pulse:infosciencePersonIdentifier": None,
                    "pulse:githubUsername": None,
                    "uuid": "f887181e-9b8c-4c55-8ee6-d34285fdbed4",
                },
                "idSource": "pulse:orcid",
                "schema:name": "Alice Smith",
                "pulse:orcidIdentifier": "0000-0002-1825-0097",
                "pulse:infosciencePersonIdentifier": None,
            }
        ),
    )

    result = asyncio.run(
        agent.run(
            {"orcid": "0000-0002-1825-0097"},
            _providers(),
        ),
    )

    assert result.data["id"] == "0000-0002-1825-0097"
    assert result.data["idSource"] == "pulse:orcid"
    assert result.stats["agent_runtime"] == "llm"


def test_llm_person_agent_raises_on_empty_context() -> None:
    """Agent raises ValueError when context contains no usable identity signal."""

    agent = LLMPersonAgentV2(llm_runtime=_FakeLLMRuntime(_valid_person_payload()))

    with pytest.raises(ValueError, match="no usable identity signal"):
        asyncio.run(
            agent.run({}, _providers()),
        )


@llm_integration
@requires_llm_credentials
def test_llm_person_agent_real_provider_call() -> None:
    """Integration test: sends a real prompt to the configured LLM provider."""

    agent = LLMPersonAgentV2()

    context = {
        "username": "octocat",
        "source_repositories": ["octocat/Hello-World"],
    }

    result = asyncio.run(agent.run(context, _providers_full()))

    assert result.data["type"] == "schema:Person"
    assert result.data["shacl"] == "pulse:PersonShape"
    assert result.model is not None
    assert result.provider is not None
    assert result.stats["agent_runtime"] == "llm"

    import json

    print("\n--- LLM Person Agent Result ---")
    print(f"Model:    {result.model}")
    print(f"Provider: {result.provider}")
    print(f"Tokens:   prompt={result.tokens_prompt}, completion={result.tokens_completion}")
    print(f"Warnings: {result.warnings}")
    print(json.dumps(result.data, indent=2, ensure_ascii=False))
