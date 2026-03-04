from __future__ import annotations

import asyncio
from typing import Any

import pytest
from jsonschema import validate

from src.v2.agents.llm.membership import LLMMembershipAgentV2
from src.v2.agents.models import ProviderSet
from src.v2.llm.runtime import LLMRuntimeError, LLMRuntimeResult
from src.v2.providers.mock_github import MockGitHubProvider

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


def _valid_membership_payload() -> dict[str, Any]:
    return {
        "id": "alice_org:epfl",
        "type": "org:Membership",
        "shacl": "pulse:MembershipShape",
        "identifiers": {
            "pulse:composite": "alice_org:epfl",
            "uuid": "f887181e-9b8c-4c55-8ee6-d34285fdbed4",
        },
        "idSource": "pulse:composite",
        "org:organization": "org:epfl",
        "org:role": "Research Engineer",
        "time:hasBeginning": "2020-01-01",
        "time:hasEnd": None,
    }


def test_llm_membership_agent_validates_payload_and_exposes_model_metadata(
    load_schema,
) -> None:
    agent = LLMMembershipAgentV2(
        llm_runtime=_FakeLLMRuntime(_valid_membership_payload()),
    )
    result = asyncio.run(
        agent.run(
            {
                "membership_seed": "alice",
                "known_persons": [{"id": "alice", "type": "schema:Person"}],
                "known_organizations": [{"id": "org:epfl", "type": "org:Organization"}],
            },
            _providers(),
        ),
    )

    schema = load_schema("agent", "membership")
    validate(instance=result.data, schema=schema)

    assert result.model == "openai/gpt-test"
    assert result.provider == "openai"
    assert result.tokens_prompt == EXPECTED_PROMPT_TOKENS
    assert result.tokens_completion == EXPECTED_COMPLETION_TOKENS
    assert result.stats["agent_runtime"] == "llm"
    assert result.stats["membership_count"] == 1


def test_llm_membership_agent_propagates_llm_runtime_error() -> None:
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

    agent = LLMMembershipAgentV2(llm_runtime=_RaisingRuntime())

    with pytest.raises(LLMRuntimeError, match="Schema validation failed after retries"):
        asyncio.run(agent.run({"membership_seed": "alice"}, _providers()))


def test_llm_membership_agent_timeout_includes_identifier_and_timeout_seconds() -> None:
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

    agent = LLMMembershipAgentV2(
        llm_runtime=_SlowRuntime(),
        llm_call_timeout_seconds=0.1,
    )

    with pytest.raises(LLMRuntimeError, match=r"alice.*timed out after 0\.1s"):
        asyncio.run(agent.run({"membership_seed": "alice"}, _providers()))


def test_llm_membership_agent_records_strict_schema_warnings() -> None:
    payload = _valid_membership_payload()
    payload["time:hasBeginning"] = "not-a-date"
    agent = LLMMembershipAgentV2(llm_runtime=_FakeLLMRuntime(payload))

    result = asyncio.run(agent.run({"membership_seed": "alice"}, _providers()))

    assert result.warnings, "Expected strict-schema warnings but got none"
    assert any("time:hasBeginning" in warning for warning in result.warnings)


def test_llm_membership_agent_appends_runtime_prompt_context_blocks() -> None:
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
                payload=_valid_membership_payload(),
                model="openai/gpt-test",
                provider="openai",
            )

    agent = LLMMembershipAgentV2(llm_runtime=_CapturingRuntime())
    upstream_json = '{"person_agent:alice":{"id":"alice"}}'
    prompt_appendix = "AFFILIATION_CONTEXT"

    asyncio.run(
        agent.run(
            {
                "membership_seed": "alice",
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
