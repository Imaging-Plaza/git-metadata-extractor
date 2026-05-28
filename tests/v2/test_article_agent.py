from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any, Callable

from jsonschema import validate

from src.v2.agents import ArticleAgentV2, ProviderSet
from src.v2.ingest.providers.base import InfoscienceProvider
from src.v2.ingest.providers.mock_github import MockGitHubProvider

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


def test_article_agent_query_blend_repository_mode_default_is_repo_only() -> None:
    """By default, the article agent only queries Infoscience by repo terms.

    Searching by contributor name or org name returns those people's full
    bibliography and over-attributes unrelated publications to the repo
    (real DOIs, false attribution). The default repo-only blend keeps
    the precision high.
    """

    agent = ArticleAgentV2(max_queries=5)

    queries = agent.build_query_blend(_build_context())

    assert queries == [
        "sdsc-ordes/gimie",
        "gimie",
    ]


def test_article_agent_query_blend_widens_when_explicitly_enabled() -> None:
    agent = ArticleAgentV2(
        max_queries=5,
        include_person_queries=True,
        include_organization_queries=True,
    )

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
    # This test exercises ranking/dedup over a wide blend (repo + persons +
    # orgs); explicitly enable the wide blend since the default is repo-only.
    agent = ArticleAgentV2(
        max_queries=6,
        include_person_queries=True,
        include_organization_queries=True,
    )

    result = asyncio.run(agent.run(_build_context(), providers))

    schema = load_schema("agent", "article")
    validate(instance=result.data, schema=schema)

    assert result.data["id"] == "https://doi.org/10.1000/graph-1"
    assert result.data["idSource"] == "schema:identifier"
    assert result.data["schema:sourceOrganization"] == "https://ror.org/02s376052"
    assert result.data["schema:author"] == [
        "https://orcid.org/0000-0002-1825-0097",
    ]
    assert any(
        warning.startswith("Dropped unresolved article author references for 1 name(s)")
        and "'Unknown Contributor'" in warning
        for warning in result.warnings
    )
    assert result.stats["ranked_candidate_count"] == EXPECTED_RANKED_ARTICLE_COUNT
    assert [article["id"] for article in result.stats["articles"]] == [
        "https://doi.org/10.1000/graph-1",
        "https://doi.org/10.1000/graph-2",
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


def test_article_agent_drops_unresolved_author_references() -> None:
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

    result = asyncio.run(agent.run(context, providers))

    assert result.data["id"] == "https://doi.org/10.1000/graph-3"
    assert result.data["schema:author"] == ["https://orcid.org/0000-0002-1825-0097"]
    assert any(
        warning.startswith("Dropped unresolved article author references for 1 name(s)")
        for warning in result.warnings
    )


def test_article_agent_normalizes_year_only_date() -> None:
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

    result = asyncio.run(agent.run(context, providers))

    assert result.data["id"] == "https://doi.org/10.1000/graph-4"
    assert result.data["schema:datePublished"] == "2016-01-01"
    assert result.stats["articles"]
    assert any(
        "Publication date provided as year-only; normalized to '2016-01-01' for schema compatibility"
        in warning
        for warning in result.warnings
    )


def test_article_agent_resolves_accent_and_name_order_variants() -> None:
    provider = _RecordingInfoscienceProvider(
        {
            "sdsc-ordes/gimie": [
                {
                    "infosciencePublicationIdentifier": "pub-variant-1",
                    "title": "Accent and Ordering Variants",
                    "doi": "10.1000/variant-1",
                    "publicationDate": "2025-04-01",
                    "authors": ["Alvarez, Jose"],
                    "url": "https://infoscience.epfl.ch/entities/publication/pub-variant-1",
                    "sourceOrganization": "Swiss Data Science Center",
                    "score": 9.0,
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
    context["known_persons"] = [
        {
            "id": "https://orcid.org/0000-0003-1234-5678",
            "schema:name": "José Alvarez",
            "pulse:githubUsername": "josealvarez",
            "github_display_name": "Jose Alvarez",
            "orcid_record": {"name": "José Alvarez"},
            "infoscience_record": {"name": "Jose Alvarez"},
        },
    ]

    result = asyncio.run(agent.run(context, providers))

    assert result.data["id"] == "https://doi.org/10.1000/variant-1"
    assert result.data["schema:author"] == ["https://orcid.org/0000-0003-1234-5678"]
    assert not any(
        "Skipped article candidate due to missing resolvable authors"
        in warning
        for warning in result.warnings
    )


def test_article_agent_skips_fully_unresolved_authors_with_count_metadata() -> None:
    provider = _RecordingInfoscienceProvider(
        {
            "sdsc-ordes/gimie": [
                {
                    "infosciencePublicationIdentifier": "pub-variant-2",
                    "title": "No Resolvable Authors",
                    "doi": "10.1000/variant-2",
                    "publicationDate": "2025-05-01",
                    "authors": ["Unknown One", "Unknown Two"],
                    "url": "https://infoscience.epfl.ch/entities/publication/pub-variant-2",
                    "sourceOrganization": "Swiss Data Science Center",
                    "score": 8.0,
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

    result = asyncio.run(agent.run(context, providers))

    assert result.data == {}
    assert result.stats["articles"] == []
    assert any(
        "Publication has no resolvable author identifiers: doi=10.1000/variant-2" in warning
        and "title='No Resolvable Authors'" in warning
        and "author_examples='Unknown One', 'Unknown Two'" in warning
        for warning in result.warnings
    )
    assert any(
        "matched_authors=0, unmatched_authors=2" in warning
        and "Skipped article candidate due to missing resolvable authors"
        in warning
        for warning in result.warnings
    )


def test_article_agent_skips_candidate_without_doi_required_by_strict_schema() -> None:
    provider = _RecordingInfoscienceProvider(
        {
            "sdsc-ordes/gimie": [
                {
                    "infosciencePublicationIdentifier": "36f14ad6-3b30-4c6a-9118-2346d8f8a83e",
                    "title": "No DOI Publication",
                    "doi": None,
                    "publicationDate": "2025-01-01",
                    "authors": ["Alice Example"],
                    "url": "https://infoscience.epfl.ch/entities/publication/36f14ad6-3b30-4c6a-9118-2346d8f8a83e",
                    "sourceOrganization": "Swiss Data Science Center",
                    "score": 7.0,
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

    result = asyncio.run(agent.run(context, providers))

    assert result.data == {}
    assert result.stats["articles"] == []
    assert any(
        "Skipped article candidate due to missing DOI required by strict schema: "
        "infoscience=36f14ad6-3b30-4c6a-9118-2346d8f8a83e"
        in warning
        and "title='No DOI Publication'" in warning
        and "url=https://infoscience.epfl.ch/entities/publication/36f14ad6-3b30-4c6a-9118-2346d8f8a83e"
        in warning
        for warning in result.warnings
    )


def test_article_agent_aggregates_missing_resolvable_author_skip_warnings() -> None:
    provider = _RecordingInfoscienceProvider(
        {
            "sdsc-ordes/gimie": [
                {
                    "infosciencePublicationIdentifier": "pub-a",
                    "title": "Unmapped Authors A",
                    "doi": "10.1000/unmapped-a",
                    "publicationDate": "2025-01-01",
                    "authors": ["Unknown One", "Unknown Two"],
                    "url": "https://infoscience.epfl.ch/entities/publication/pub-a",
                    "sourceOrganization": "Swiss Data Science Center",
                    "score": 10.0,
                },
                {
                    "infosciencePublicationIdentifier": "pub-b",
                    "title": "Unmapped Authors B",
                    "doi": "10.1000/unmapped-b",
                    "publicationDate": "2025-02-01",
                    "authors": ["Unknown Three"],
                    "url": "https://infoscience.epfl.ch/entities/publication/pub-b",
                    "sourceOrganization": "Swiss Data Science Center",
                    "score": 9.0,
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

    result = asyncio.run(agent.run(context, providers))

    assert result.data == {}
    assert result.stats["articles"] == []
    assert any(
        "Skipped article candidates due to missing resolvable authors: count=2"
        in warning
        for warning in result.warnings
    )
    assert not any(
        warning.startswith(
            "Skipped article candidate due to missing resolvable authors:",
        )
        and "10.1000/unmapped" in warning
        for warning in result.warnings
    )
