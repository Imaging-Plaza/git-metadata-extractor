from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any, Callable

from jsonschema import validate

from src.v2.agents import ArticleAgentV2, ProviderSet
from src.v2.providers.base import InfoscienceProvider
from src.v2.providers.mock_github import MockGitHubProvider

EXPECTED_RANKED_ARTICLE_COUNT = 2


class _RecordingInfoscienceProvider(InfoscienceProvider):
    def __init__(self, publications_by_query: dict[str, list[dict[str, Any]]]) -> None:
        self._publications_by_query = publications_by_query
        self.queries: list[str] = []

    def search_person(self, query: str) -> list[dict[str, Any]]:
        del query
        return []

    def search_orgunit(self, query: str) -> list[dict[str, Any]]:
        del query
        return []

    def search_publications(self, query: str) -> list[dict[str, Any]]:
        self.queries.append(query)
        return deepcopy(self._publications_by_query.get(query, []))


def _build_context(*, detected_type: str = "repository") -> dict[str, Any]:
    return {
        "detected_type": detected_type,
        "full_name": "sdsc-ordes/gimie",
        "username": "alice",
        "org_name": "Swiss Data Science Center",
        "known_persons": [
            {
                "id": "https://orcid.org/0000-0002-1825-0097",
                "schema:name": "Alice Example",
                "pulse:githubUsername": "alice",
            },
            {
                "id": "https://orcid.org/0000-0002-1825-0097",
                "schema:name": "Alice Example",
                "pulse:githubUsername": "alice",
            },
        ],
        "known_organizations": [
            {
                "id": "https://ror.org/02s376052",
                "schema:name": "Swiss Data Science Center",
                "pulse:githubOrganizationHandle": "sdsc-ordes",
            },
        ],
    }


def test_article_agent_query_blend_repository_mode_dedupes_and_caps() -> None:
    agent = ArticleAgentV2(max_queries=5)

    queries = agent.build_query_blend(_build_context())

    assert queries == [
        "sdsc-ordes/gimie",
        "gimie",
        "Alice Example",
        "alice",
        "Swiss Data Science Center",
    ]


def test_article_agent_query_blend_includes_root_terms_for_user_and_organization() -> None:
    agent = ArticleAgentV2(max_queries=4)

    user_queries = agent.build_query_blend(_build_context(detected_type="user"))
    organization_queries = agent.build_query_blend(
        _build_context(detected_type="organization"),
    )

    assert user_queries[0] == "alice"
    assert organization_queries[0] == "Swiss Data Science Center"


def test_article_agent_ranks_dedupes_and_maps_links(
    load_schema: Callable[[str, str], dict[str, Any]],
) -> None:
    provider = _RecordingInfoscienceProvider(
        {
            "sdsc-ordes/gimie": [
                {
                    "infosciencePublicationIdentifier": "pub-1",
                    "title": "Graph Methods for Metadata",
                    "doi": "10.1000/graph-1",
                    "publicationDate": "2025-03-01",
                    "authors": ["Alice Example", "Unknown Contributor"],
                    "url": "https://infoscience.epfl.ch/entities/publication/pub-1",
                    "sourceOrganization": "Swiss Data Science Center",
                    "score": 14.2,
                },
                {
                    "infosciencePublicationIdentifier": "pub-1-duplicate",
                    "title": "Graph Methods for Metadata (Duplicate)",
                    "doi": "10.1000/graph-1",
                    "publicationDate": "2025-03-01",
                    "authors": ["Alice Example"],
                    "url": "https://infoscience.epfl.ch/entities/publication/pub-1-duplicate",
                    "sourceOrganization": "Swiss Data Science Center",
                    "score": 10.0,
                },
            ],
            "Alice Example": [
                {
                    "infosciencePublicationIdentifier": "pub-2",
                    "title": "Open Metadata Pipelines",
                    "doi": "10.1000/graph-2",
                    "publicationDate": "2024-07-15",
                    "authors": ["Alice Example"],
                    "url": "https://infoscience.epfl.ch/entities/publication/pub-2",
                    "sourceOrganization": "Swiss Data Science Center",
                    "score": 12.5,
                },
            ],
        },
    )
    providers = ProviderSet(
        github=MockGitHubProvider(),
        infoscience=provider,
    )
    agent = ArticleAgentV2(max_queries=6)

    result = asyncio.run(agent.run(_build_context(), providers))

    schema = load_schema("agent", "article")
    validate(instance=result.data, schema=schema)

    assert result.data["id"] == "10.1000/graph-1"
    assert result.data["idSource"] == "schema:identifier"
    assert result.data["schema:sourceOrganization"] == "https://ror.org/02s376052"
    assert result.data["schema:author"] == [
        "https://orcid.org/0000-0002-1825-0097",
        "Unknown Contributor",
    ]
    assert any(
        warning.startswith("Deferred article author resolution for 1 name(s);")
        and "'Unknown Contributor'" in warning
        for warning in result.warnings
    )
    assert result.stats["ranked_candidate_count"] == EXPECTED_RANKED_ARTICLE_COUNT
    assert [article["id"] for article in result.stats["articles"]] == [
        "10.1000/graph-1",
        "10.1000/graph-2",
    ]
    assert provider.queries[:3] == [
        "sdsc-ordes/gimie",
        "gimie",
        "Alice Example",
    ]


def test_article_agent_handles_empty_provider_results_without_failure() -> None:
    provider = _RecordingInfoscienceProvider({})
    providers = ProviderSet(
        github=MockGitHubProvider(),
        infoscience=provider,
    )
    agent = ArticleAgentV2()

    result = asyncio.run(agent.run(_build_context(), providers))

    assert result.data == {}
    assert "No publications returned for blended article queries" in result.warnings
    assert result.stats["queries"]
    assert result.stats["articles"] == []


def test_article_agent_drops_unresolved_author_references_when_synthetic_fallbacks_disabled() -> None:
    provider = _RecordingInfoscienceProvider(
        {
            "sdsc-ordes/gimie": [
                {
                    "infosciencePublicationIdentifier": "pub-3",
                    "title": "Mapped Authors Only",
                    "doi": "10.1000/graph-3",
                    "publicationDate": "2025-03-01",
                    "authors": ["Alice Example", "Unknown Contributor"],
                    "url": "https://infoscience.epfl.ch/entities/publication/pub-3",
                    "sourceOrganization": "Swiss Data Science Center",
                    "score": 10.0,
                },
            ],
        },
    )
    providers = ProviderSet(
        github=MockGitHubProvider(),
        infoscience=provider,
    )
    agent = ArticleAgentV2(max_queries=3)
    context = _build_context()
    context["allow_synthetic_fallbacks"] = False

    result = asyncio.run(agent.run(context, providers))

    assert result.data["id"] == "10.1000/graph-3"
    assert result.data["schema:author"] == ["https://orcid.org/0000-0002-1825-0097"]
    assert any(
        warning.startswith("Dropped unresolved article author references for 1 name(s)")
        for warning in result.warnings
    )


def test_article_agent_skips_candidate_with_year_only_date_when_synthetic_fallbacks_disabled() -> None:
    provider = _RecordingInfoscienceProvider(
        {
            "sdsc-ordes/gimie": [
                {
                    "infosciencePublicationIdentifier": "pub-4",
                    "title": "Year Only Date",
                    "doi": "10.1000/graph-4",
                    "publicationDate": "2016",
                    "authors": ["Alice Example"],
                    "url": "https://infoscience.epfl.ch/entities/publication/pub-4",
                    "sourceOrganization": "Swiss Data Science Center",
                    "score": 10.0,
                },
            ],
        },
    )
    providers = ProviderSet(
        github=MockGitHubProvider(),
        infoscience=provider,
    )
    agent = ArticleAgentV2(max_queries=3)
    context = _build_context()
    context["allow_synthetic_fallbacks"] = False

    result = asyncio.run(agent.run(context, providers))

    assert result.data == {}
    assert result.stats["articles"] == []
    assert any(
        "Skipped article candidate due to invalid publication date with synthetic fallbacks disabled"
        in warning
        for warning in result.warnings
    )
