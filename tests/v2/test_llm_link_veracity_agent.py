from __future__ import annotations

import asyncio
from typing import Any

import pytest

from git_metadata_extractor.agents.llm.link_veracity import LLMLinkVeracityAgentV2
from git_metadata_extractor.agents.models import ProviderSet
from git_metadata_extractor.agents.llm.runtime import LLMRuntimeError, LLMRuntimeResult
from git_metadata_extractor.providers.mock_github import MockGitHubProvider

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
        del output_type
        assert system_prompt
        assert user_prompt
        assert tools
        return LLMRuntimeResult(
            payload=dict(self._payload),
            model="openai/gpt-test",
            provider="openai",
            tokens_prompt=EXPECTED_PROMPT_TOKENS,
            tokens_completion=EXPECTED_COMPLETION_TOKENS,
        )


def _providers() -> ProviderSet:
    return ProviderSet(github=MockGitHubProvider())


def _valid_payload(link: str) -> dict[str, Any]:
    return {
        "link": link,
        "relationship_supported": True,
        "relationship_summary": "The page title and body support this relation.",
        "fetched_successfully": True,
    }


def test_llm_link_veracity_agent_returns_boolean_verdict_with_metadata() -> None:
    link = "https://example.org/resource"
    agent = LLMLinkVeracityAgentV2(llm_runtime=_FakeLLMRuntime(_valid_payload(link)))
    result = asyncio.run(
        agent.run(
            {
                "link": link,
                "source_entity_id": "urn:entity:source",
                "predicate": "schema:url",
            },
            _providers(),
        ),
    )

    assert result.data["link"] == link
    assert result.data["relationship_supported"] is True
    assert result.model == "openai/gpt-test"
    assert result.provider == "openai"
    assert result.tokens_prompt == EXPECTED_PROMPT_TOKENS
    assert result.tokens_completion == EXPECTED_COMPLETION_TOKENS
    assert result.stats["agent_runtime"] == "llm"
    assert result.stats["relationship_supported"] is True


def test_llm_link_veracity_agent_propagates_runtime_error() -> None:
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

    agent = LLMLinkVeracityAgentV2(llm_runtime=_RaisingRuntime())
    with pytest.raises(LLMRuntimeError, match="Schema validation failed after retries"):
        asyncio.run(agent.run({"link": "https://example.org"}, _providers()))


def test_llm_link_veracity_agent_timeout_includes_link_and_timeout_seconds() -> None:
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

    agent = LLMLinkVeracityAgentV2(
        llm_runtime=_SlowRuntime(),
        llm_call_timeout_seconds=0.1,
    )
    with pytest.raises(LLMRuntimeError, match=r"https://example\.org.*timed out after 0\.1s"):
        asyncio.run(agent.run({"link": "https://example.org"}, _providers()))


def test_llm_link_veracity_agent_appends_runtime_prompt_context_blocks() -> None:
    captured_user_prompts: list[str] = []
    captured_tool_names: list[str] = []

    class _CapturingRuntime:
        async def run_json_prompt(
            self,
            *,
            system_prompt: str,
            user_prompt: str,
            output_type: Any = None,
            tools: Any = None,
        ) -> LLMRuntimeResult:
            del system_prompt, output_type
            captured_user_prompts.append(user_prompt)
            captured_tool_names.extend(getattr(tool, "name", "") for tool in (tools or []))
            return LLMRuntimeResult(
                payload=_valid_payload("https://example.org"),
                model="openai/gpt-test",
                provider="openai",
            )

    agent = LLMLinkVeracityAgentV2(llm_runtime=_CapturingRuntime())
    upstream_json = '{"repo_agent":{"id":"repo-root"}}'
    prompt_appendix = "RAW_RELATIONSHIPS_BLOCK"

    asyncio.run(
        agent.run(
            {
                "link": "https://example.org",
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
    assert captured_tool_names == ["fetch_link_content_via_selenium"]
