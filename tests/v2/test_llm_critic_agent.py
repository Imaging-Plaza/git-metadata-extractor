from __future__ import annotations

import asyncio
from typing import Any

from src.v2.agents.llm.critic.agent import LLMCriticAgentV2
from src.v2.agents.models import ProviderSet
from src.v2.agents.llm.runtime import LLMRuntimeResult
from src.v2.ingest.providers.mock_github import MockGitHubProvider


def _providers() -> ProviderSet:
    return ProviderSet(github=MockGitHubProvider())


def test_llm_critic_agent_exposes_duckduckgo_and_owner_context_tools() -> None:
    captured_tools: list[Any] = []
    captured_user_prompt: list[str] = []

    class _CapturingRuntime:
        async def run_json_prompt(
            self,
            *,
            system_prompt: str,
            user_prompt: str,
            output_type: Any = None,
            tools: Any = None,
        ) -> LLMRuntimeResult:
            del output_type
            assert "relevance critic" in system_prompt.lower()
            captured_user_prompt.append(user_prompt)
            captured_tools.extend(tools or [])
            return LLMRuntimeResult(
                payload={
                    "organizations": [],
                    "persons": [],
                    "repositories": [],
                    "articles": [],
                },
                model="openai/gpt-test",
                provider="openai",
                tokens_prompt=10,
                tokens_completion=12,
            )

    agent = LLMCriticAgentV2(llm_runtime=_CapturingRuntime())
    result = asyncio.run(
        agent.run(
            {
                "source_url": "https://github.com/sdsc-ordes/gimie",
                "detected_type": "repository",
                "initial_context": {
                    "repository": {
                        "full_name": "sdsc-ordes/gimie",
                        "metadata": {
                            "html_url": "https://github.com/sdsc-ordes/gimie",
                            "owner": {"login": "sdsc-ordes", "type": "Organization"},
                        },
                    },
                },
                "pipeline_outputs": {},
                "reconciled_entities": {
                    "organizations": [],
                    "persons": [],
                    "repositories": [],
                    "articles": [],
                },
                "memberships": [],
                "contributions": [],
            },
            _providers(),
        ),
    )

    assert result.data["organizations"] == []
    tool_names = [getattr(tool, "name", None) for tool in captured_tools]
    assert "search_on_the_internet" in tool_names
    assert "get_github_organization_metadata" in tool_names
    assert captured_user_prompt
    assert "owner_provenance" in captured_user_prompt[0]
    assert "sdsc-ordes" in captured_user_prompt[0]

