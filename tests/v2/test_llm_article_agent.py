from __future__ import annotations

import asyncio
from typing import Any

import pytest
from jsonschema import validate

from src.v2.agents.llm.article import LLMArticleAgentV2
from src.v2.agents.models import ProviderSet
from src.v2.agents.llm.runtime import LLMRuntimeError, LLMRuntimeResult
from src.v2.ingest.providers.mock_github import MockGitHubProvider

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


def _valid_article_payload() -> dict[str, Any]:
    return {
        "id": "10.1000/article-1",
        "type": "schema:ScholarlyArticle",
        "shacl": "pulse:ArticleShape",
        "identifiers": {
            "schema:identifier": "10.1000/article-1",
            "pulse:infoscienceArticleIdentifier": None,
            "uuid": "f887181e-9b8c-4c55-8ee6-d34285fdbed4",
        },
        "idSource": "schema:identifier",
        "schema:name": "A paper",
        "schema:identifier": "10.1000/article-1",
        "schema:datePublished": "2024-01-01",
        "schema:author": ["alice"],
        "pulse:infoscienceArticleIdentifier": None,
        "schema:sourceOrganization": "org:epfl",
    }


def test_llm_article_agent_validates_payload_and_exposes_model_metadata(
    load_schema,
) -> None:
    agent = LLMArticleAgentV2(llm_runtime=_FakeLLMRuntime(_valid_article_payload()))
    result = asyncio.run(
        agent.run(
            {
                "article_seed": "octocat/Hello-World",
                "known_persons": [{"id": "alice", "type": "schema:Person"}],
            },
            _providers(),
        ),
    )

    schema = load_schema("agent", "article")
    validate(instance=result.data, schema=schema)

    assert result.model == "openai/gpt-test"
    assert result.provider == "openai"
    assert result.tokens_prompt == EXPECTED_PROMPT_TOKENS
    assert result.tokens_completion == EXPECTED_COMPLETION_TOKENS
    assert result.stats["agent_runtime"] == "llm"
    assert result.stats["article_count"] == 1


def test_llm_article_agent_propagates_llm_runtime_error() -> None:
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

    agent = LLMArticleAgentV2(llm_runtime=_RaisingRuntime())

    with pytest.raises(LLMRuntimeError, match="Schema validation failed after retries"):
        asyncio.run(agent.run({"article_seed": "owner/repo"}, _providers()))


def test_llm_article_agent_timeout_includes_identifier_and_timeout_seconds() -> None:
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

    agent = LLMArticleAgentV2(
        llm_runtime=_SlowRuntime(),
        llm_call_timeout_seconds=0.1,
    )

    with pytest.raises(LLMRuntimeError, match=r"owner/repo.*timed out after 0\.1s"):
        asyncio.run(agent.run({"article_seed": "owner/repo"}, _providers()))


def test_llm_article_agent_records_strict_schema_warnings() -> None:
    payload = _valid_article_payload()
    payload["schema:datePublished"] = "not-a-date"
    agent = LLMArticleAgentV2(llm_runtime=_FakeLLMRuntime(payload))

    result = asyncio.run(agent.run({"article_seed": "owner/repo"}, _providers()))

    assert result.warnings, "Expected strict-schema warnings but got none"
    assert any("schema:datePublished" in warning for warning in result.warnings)


def test_llm_article_agent_appends_runtime_prompt_context_blocks() -> None:
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
                payload=_valid_article_payload(),
                model="openai/gpt-test",
                provider="openai",
            )

    agent = LLMArticleAgentV2(llm_runtime=_CapturingRuntime())
    upstream_json = '{"repo_agent":{"id":"repo-root"}}'
    prompt_appendix = "README_FRAGMENT"

    asyncio.run(
        agent.run(
            {
                "article_seed": "owner/repo",
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
    assert "generate_uuid_v4" in captured_tool_names
    assert "fetch_link_content_via_selenium" in captured_tool_names
