from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import scripts.v2.run_llm_repo_and_persons as run_script
from git_metadata_extractor.agents.models import AgentResult, ProviderSet


def test_run_llm_repo_and_persons_handles_partial_failures_and_prints_entities(
    monkeypatch,
    capsys,
) -> None:
    class _FakeRepositoryAgent:
        async def run(self, context: dict[str, Any], providers: ProviderSet) -> AgentResult:
            del context, providers
            return AgentResult(
                data={"id": "repo-id"},
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
                    data={"id": "ok-user", "type": "schema:Person"},
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

    async def _fake_gather_context(
        _detected_type: str,
        _url_info: Any,
        _providers: ProviderSet,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            context={
                "repository": {
                    "metadata": {"full_name": "octo/repo"},
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

    asyncio.run(
        run_script._run(
            "octo/repo",
            person_timeout_seconds=180.0,
            max_concurrency=3,
            heartbeat_seconds=0.01,
        ),
    )

    captured = capsys.readouterr()
    stdout = captured.out
    assert "heartbeat:" in stdout
    assert "Person summary: ok=1 timeout=1 error=1" in stdout
    assert "Failed contributors:" in stdout
    assert "timeout-user: timeout" in stdout
    assert "error-user: error" in stdout
    assert "Person entities:" in stdout

    entities_section = stdout.split("Person entities:\n", maxsplit=1)[1]
    entities_json = entities_section.split(f"\n{run_script._SEP}", maxsplit=1)[0]
    parsed_entities = json.loads(entities_json)
    assert parsed_entities == [{"id": "ok-user", "type": "schema:Person"}]
