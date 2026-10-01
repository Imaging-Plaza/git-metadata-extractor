from __future__ import annotations

from pathlib import Path

import pytest

from git_metadata_extractor.providers.base import (
    INFOSCIENCE_PUBLICATION_OPTIONAL_FIELDS,
    INFOSCIENCE_PUBLICATION_REQUIRED_FIELDS,
)
from git_metadata_extractor.providers.mock_infoscience import MockInfoscienceProvider

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "providers" / "infoscience"
MULTI_HIT_MIN_RESULTS = 2
EXPECTED_PUBLICATION_COUNT = 2


@pytest.fixture(scope="module")
def provider() -> MockInfoscienceProvider:
    return MockInfoscienceProvider(fixture_root=FIXTURE_ROOT)


def test_search_person_single_hit_returns_exact_single_match(
    provider: MockInfoscienceProvider,
) -> None:
    result = provider.search_person("alice smith")

    assert len(result) == 1
    assert "infosciencePersonIdentifier" in result[0]


def test_search_person_multi_hit_returns_ambiguous_candidates(
    provider: MockInfoscienceProvider,
) -> None:
    result = provider.search_person("smith")

    assert len(result) >= MULTI_HIT_MIN_RESULTS


def test_empty_person_search_returns_empty_list(
    provider: MockInfoscienceProvider,
) -> None:
    result = provider.search_person("missing profile")

    assert result == []


def test_orgunit_and_publication_queries_return_structured_results(
    provider: MockInfoscienceProvider,
) -> None:
    orgunits = provider.search_orgunit("epfl")
    publications = provider.search_publications("geodata")

    assert orgunits
    assert publications
    assert "infoscienceOrgUnitIdentifier" in orgunits[0]
    assert "infosciencePublicationIdentifier" in publications[0]
    assert publications[0]["publicationDate"] == "2024-05-12"
    assert publications[0]["authors"] == ["Alice Smith", "Marco Weber"]


def test_mock_publication_results_match_infoscience_contract(
    provider: MockInfoscienceProvider,
) -> None:
    publications = provider.search_publications("geodata")

    assert len(publications) == EXPECTED_PUBLICATION_COUNT
    assert [
        publication["infosciencePublicationIdentifier"]
        for publication in publications
    ] == [
        "ac893a20-d10d-4f8a-91ec-9cb7631f60fc",
        "8f946f6f-f9f9-4f0e-ab2c-9f635a36b2bc",
    ]

    for publication in publications:
        for field_name in INFOSCIENCE_PUBLICATION_REQUIRED_FIELDS:
            assert field_name in publication
        for field_name in INFOSCIENCE_PUBLICATION_OPTIONAL_FIELDS:
            if field_name in publication:
                assert publication[field_name] is None or isinstance(
                    publication[field_name],
                    str,
                )
