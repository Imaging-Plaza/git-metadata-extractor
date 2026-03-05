from __future__ import annotations

import asyncio
from typing import Any

from src.v2.agents import ProviderSet
from src.v2.agents.models import AgentResult
from src.v2.pipeline.stages import (
    collect_unique_http_link_contexts,
    run_link_veracity_stage,
)
from src.v2.providers.mock_github import MockGitHubProvider


def _providers() -> ProviderSet:
    return ProviderSet(github=MockGitHubProvider())


def test_collect_unique_http_link_contexts_deduplicates_nested_links() -> None:
    payload = {
        "@context": {},
        "@graph": [
            {
                "@id": "urn:node:1",
                "schema:url": "https://example.org/a",
                "schema:license": {"@id": "https://spdx.org/licenses/MIT.html"},
                "nested": {
                    "values": [
                        "https://example.org/a",
                        "https://example.org/b",
                    ],
                },
            },
            {
                "@id": "urn:node:2",
                "schema:url": "https://example.org/b",
                "schema:sameAs": [{"@id": "https://example.org/c"}],
            },
        ],
    }

    contexts = collect_unique_http_link_contexts(payload)

    assert [context["link"] for context in contexts] == [
        "https://example.org/a",
        "https://example.org/b",
        "https://example.org/c",
        "https://spdx.org/licenses/MIT.html",
    ]


def test_run_link_veracity_stage_counts_and_warnings(monkeypatch) -> None:
    import src.v2.pipeline.stages.link_veracity as stage_module

    class _FakeVerifier:
        def __init__(
            self,
            *,
            llm_runtime: Any | None = None,
            llm_call_timeout_seconds: float = 180.0,
        ) -> None:
            del llm_runtime, llm_call_timeout_seconds

        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del providers
            link = context["link"]
            if link.endswith("/fail"):
                raise RuntimeError("link timeout")
            return AgentResult(
                data={
                    "link": link,
                    "relationship_supported": link.endswith("/yes"),
                    "relationship_summary": "ok",
                    "fetched_successfully": True,
                },
                model="openai/gpt-test",
                provider="openai",
                tokens_prompt=10,
                tokens_completion=5,
            )

    monkeypatch.setattr(stage_module, "LLMLinkVeracityAgentV2", _FakeVerifier)

    payload = {
        "@context": {},
        "@graph": [
            {
                "@id": "urn:node:1",
                "schema:url": "https://example.org/yes",
                "schema:license": "https://example.org/no",
            },
            {
                "@id": "urn:node:2",
                "schema:url": "https://example.org/fail",
            },
        ],
    }

    result = asyncio.run(
        run_link_veracity_stage(
            jsonld_payload=payload,
            source_url="https://github.com/owner/repo",
            providers=_providers(),
            max_concurrency=2,
        ),
    )

    assert result.checked_count == 3
    assert result.supported_count == 1
    assert result.unsupported_count == 1
    assert result.failed_count == 1
    assert any("unsupported relationship" in warning for warning in result.warnings)
    assert any("check failed" in warning for warning in result.warnings)


def test_run_link_veracity_stage_handles_no_links() -> None:
    payload = {
        "@context": {},
        "@graph": [
            {"@id": "urn:node:1", "schema:name": "Example"},
            {"@id": "urn:node:2", "schema:author": ["urn:node:1"]},
        ],
    }

    result = asyncio.run(
        run_link_veracity_stage(
            jsonld_payload=payload,
            source_url="https://github.com/owner/repo",
            providers=_providers(),
        ),
    )

    assert result.checked_count == 0
    assert result.records == []
    assert result.warnings == []
