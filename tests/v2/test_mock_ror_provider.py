from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import pytest

from src.v2.providers.base import ProviderNotFoundError, RORProvider
from src.v2.providers.mock_ror import MockRORProvider

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "providers" / "ror"
MIN_FIXTURE_COUNT = 5
MIN_LABEL_VARIANTS = 2
EXPECTED_FIXTURE_FILES = {
    "not_found.json",
    "org_detail.json",
    "org_with_aliases.json",
    "parent_org.json",
    "search_results.json",
}


@pytest.fixture(scope="module")
def provider() -> MockRORProvider:
    return MockRORProvider(fixture_root=FIXTURE_ROOT)


def test_mock_ror_provider_implements_base_interface(provider: MockRORProvider) -> None:
    assert isinstance(provider, RORProvider)
    assert not MockRORProvider.__abstractmethods__


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


def test_ror_fixture_catalog_contains_required_files() -> None:
    fixture_files = {path.name for path in FIXTURE_ROOT.glob("*.json")}

    assert EXPECTED_FIXTURE_FILES.issubset(fixture_files)
    assert len(fixture_files) >= MIN_FIXTURE_COUNT


def test_ror_fixture_files_are_valid_json() -> None:
    for fixture_path in sorted(FIXTURE_ROOT.glob("*.json")):
        with fixture_path.open(encoding="utf-8") as fixture_file:
            payload = json.load(fixture_file)

        assert isinstance(payload, dict)


def test_ror_fixtures_expose_expected_top_level_fields(
    load_fixture: Callable[[str, str], Any],
) -> None:
    org_detail = load_fixture("providers/ror", "org_detail")
    search_results = load_fixture("providers/ror", "search_results")

    assert "organization" in org_detail
    assert "items" in search_results
    assert isinstance(search_results["items"], list)
