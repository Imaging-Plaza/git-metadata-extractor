from __future__ import annotations

from pathlib import Path

import pytest

from git_metadata_extractor.providers.base import ProviderNotFoundError
from git_metadata_extractor.providers.mock_ror import MockRORProvider

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "providers" / "ror"
MIN_LABEL_VARIANTS = 2


@pytest.fixture(scope="module")
def provider() -> MockRORProvider:
    return MockRORProvider(fixture_root=FIXTURE_ROOT)


def test_get_organization_returns_expected_detail_shape(
    provider: MockRORProvider,
) -> None:
    organization = provider.get_organization("https://ror.org/02s376052")

    assert organization["name"] == "Ecole Polytechnique Federale de Lausanne"
    assert organization["aliases"]
    assert organization["types"]
    assert organization["country"]["country_name"] == "Switzerland"
    assert "relationships" in organization


def test_search_organizations_returns_ranked_candidates(
    provider: MockRORProvider,
) -> None:
    results = provider.search_organizations("epfl")

    assert results
    scores = [item["score"] for item in results]
    assert scores == sorted(scores, reverse=True)


def test_org_with_aliases_contains_labels_acronyms_and_alternate_names(
    provider: MockRORProvider,
) -> None:
    alias_variant = provider.get_organization("03yrm5c26")

    assert alias_variant["aliases"]
    assert alias_variant["acronyms"]
    assert len(alias_variant["labels"]) >= MIN_LABEL_VARIANTS


def test_get_organization_not_found_raises_provider_not_found(
    provider: MockRORProvider,
) -> None:
    with pytest.raises(ProviderNotFoundError):
        provider.get_organization("https://ror.org/000000000")
