from __future__ import annotations

import asyncio
from typing import Any

from src.v2.agents.llm.context_summary import LLMContextSummaryAgentV2
from src.v2.agents.models import ProviderSet
from src.v2.agents.llm.runtime import LLMRuntimeError, LLMRuntimeResult
from src.v2.ingest.providers.mock_github import MockGitHubProvider


def _providers() -> ProviderSet:
    return ProviderSet(github=MockGitHubProvider())


def test_llm_context_summary_agent_compiles_markdown_and_exposes_grep_tool() -> None:
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
            usage_limits: Any = None,
        ) -> LLMRuntimeResult:
            del output_type, usage_limits
            # Agent defaults to scout mode (`V2_CONTEXT_SUMMARY_SCOUT_MODE`)
            # in many envs; both the legacy compiler prompt and the scout
            # prompt share the "repository" framing.
            assert "repository" in system_prompt.lower()
            captured_user_prompt.append(user_prompt)
            captured_tools.extend(tools or [])
            return LLMRuntimeResult(
                payload={"summary_markdown": "# Summary\n- signal"},
                model="openai/gpt-test",
                provider="openai",
                tokens_prompt=21,
                tokens_completion=34,
            )

    agent = LLMContextSummaryAgentV2(llm_runtime=_CapturingRuntime())
    result = asyncio.run(
        agent.run(
            {
                "detected_type": "repository",
                "source_url": "https://github.com/owner/repo",
                "gathered_context": {
                    "repository": {
                        "full_name": "owner/repo",
                        "readme_content": "# Hello\nUse uv",
                        "gimie_jsonld": {"@id": "https://github.com/owner/repo"},
                        "repository_files": [
                            {"path": "pyproject.toml", "content": "[tool.uv]\n"},
                        ],
                    },
                },
            },
            _providers(),
        ),
    )

    assert result.data["summary_markdown"].startswith("# Summary")
    assert result.model == "openai/gpt-test"
    assert result.provider == "openai"
    assert result.tokens_prompt == 21
    assert result.tokens_completion == 34
    assert result.stats["document_count"] == 3
    assert captured_tools
    tool_names = [getattr(tool, "name", None) for tool in captured_tools]
    assert "grep_repository_corpus" in tool_names
    assert "search_on_the_internet" in tool_names
    assert captured_user_prompt
    assert "corpus_manifest" in captured_user_prompt[0]


def test_llm_context_summary_agent_is_fail_open_on_runtime_error() -> None:
    class _FailingRuntime:
        async def run_json_prompt(
            self,
            *,
            system_prompt: str,
            user_prompt: str,
            output_type: Any = None,
            tools: Any = None,
            usage_limits: Any = None,
        ) -> LLMRuntimeResult:
            del system_prompt, user_prompt, output_type, tools, usage_limits
            raise LLMRuntimeError("boom")

    agent = LLMContextSummaryAgentV2(llm_runtime=_FailingRuntime())
    result = asyncio.run(
        agent.run(
            {
                "detected_type": "repository",
                "source_url": "https://github.com/owner/repo",
                "gathered_context": {"repository": {"full_name": "owner/repo", "readme_content": "README"}},
            },
            _providers(),
        ),
    )

    assert result.is_partial is True
    assert result.failure_reason == "boom"
    assert result.data["summary_markdown"] == ""
    assert any("proceeding without compiled summary" in warning for warning in result.warnings)
