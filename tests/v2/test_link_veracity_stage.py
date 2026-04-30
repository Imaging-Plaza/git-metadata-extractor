from __future__ import annotations

import asyncio
from typing import Any

from src.v2.agents import ProviderSet
from src.v2.agents.models import AgentResult
from src.v2.pipeline.stages import (
    AssembledOutput,
    apply_link_pruning_to_assembled_output,
    collect_unique_http_link_contexts,
    run_link_veracity_stage,
)
from src.v2.ingest.providers.mock_github import MockGitHubProvider


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
            cache: Any | None = None,
        ) -> None:
            del llm_runtime, llm_call_timeout_seconds, cache

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
                    "fetched_successfully": not link.endswith("/fetch-fail"),
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
            {
                "@id": "urn:node:3",
                "schema:url": "https://example.org/fetch-fail",
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

    assert result.checked_count == 4
    assert result.supported_count == 1
    assert result.unsupported_count == 1
    assert result.failed_count == 2
    assert any("unsupported relationship" in warning for warning in result.warnings)
    assert any("check failed" in warning for warning in result.warnings)
    assert any("fetch failed" in warning for warning in result.warnings)
    assert "https://example.org/fetch-fail" in result.invalid_links


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


def test_run_link_veracity_stage_scans_entities_and_derives_article_doi_link(monkeypatch) -> None:
    import src.v2.pipeline.stages.link_veracity as stage_module

    class _FakeVerifier:
        def __init__(
            self,
            *,
            llm_runtime: Any | None = None,
            llm_call_timeout_seconds: float = 180.0,
            cache: Any | None = None,
        ) -> None:
            del llm_runtime, llm_call_timeout_seconds, cache

        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del providers
            return AgentResult(
                data={
                    "link": context["link"],
                    "relationship_supported": True,
                    "relationship_summary": "ok",
                    "fetched_successfully": True,
                },
            )

    monkeypatch.setattr(stage_module, "LLMLinkVeracityAgentV2", _FakeVerifier)

    entities = [
        {
            "id": "https://doi.org/10.1000/example",
            "type": "schema:ScholarlyArticle",
            "schema:identifier": "10.1000/example",
            "schema:url": "https://example.org/article",
        },
    ]

    result = asyncio.run(
        run_link_veracity_stage(
            entities=entities,
            source_url="https://github.com/owner/repo",
            providers=_providers(),
        ),
    )

    # DOI url == entity id, so it's a self-reference and skipped by veracity.
    # Only the distinct schema:url link is checked.
    assert result.checked_count == 1
    assert result.article_identifier_link_map["https://doi.org/10.1000/example"] == "https://doi.org/10.1000/example"
    assert "https://example.org/article" in result.entity_link_map["https://doi.org/10.1000/example"]
    assert "https://doi.org/10.1000/example" in result.entity_link_map["https://doi.org/10.1000/example"]


def test_apply_link_pruning_to_assembled_output_prunes_links_and_drops_entity() -> None:
    assembled = AssembledOutput(
        root_entity={
            "id": "https://github.com/owner/repo",
            "type": "schema:SoftwareSourceCode",
            "schema:url": "https://github.com/owner/repo",
            "schema:license": "https://example.org/bad-license",
        },
        related_entities=[
            {
                "id": "https://doi.org/10.1000/bad-doi",
                "type": "schema:ScholarlyArticle",
                "schema:identifier": "10.1000/bad-doi",
                "schema:url": "https://example.org/bad-article",
            },
        ],
    )
    updated, warnings = apply_link_pruning_to_assembled_output(
        assembled=assembled,
        invalid_links={
            "https://example.org/bad-license",
            "https://doi.org/10.1000/bad-doi",
            "https://example.org/bad-article",
        },
        entity_link_map={
            "https://github.com/owner/repo": [
                "https://github.com/owner/repo",
                "https://example.org/bad-license",
            ],
            "https://doi.org/10.1000/bad-doi": [
                "https://doi.org/10.1000/bad-doi",
                "https://example.org/bad-article",
            ],
        },
        article_identifier_link_map={
            "https://doi.org/10.1000/bad-doi": "https://doi.org/10.1000/bad-doi",
        },
    )

    assert isinstance(updated.root_entity, dict)
    assert updated.root_entity["schema:url"] == "https://github.com/owner/repo"
    assert updated.root_entity["schema:license"] is None
    assert updated.related_entities == []
    assert len(updated.excluded_entities) == 1
    assert updated.excluded_entities[0]["entity_type"] == "article"
    assert any("Removed invalid link(s) from entity 'https://github.com/owner/repo'" in warning for warning in warnings)
    assert any("Removed article entity 'https://doi.org/10.1000/bad-doi'" in warning for warning in warnings)
