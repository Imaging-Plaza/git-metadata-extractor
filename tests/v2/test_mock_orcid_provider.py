from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import pytest

from src.v2.ingest.providers.base import ORCIDProvider, ProviderNotFoundError
from src.v2.ingest.providers.mock_orcid import MockORCIDProvider

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "providers" / "orcid"
MIN_FIXTURE_COUNT = 4
MIN_EMPLOYMENT_ENTRIES = 2
EXPECTED_FIXTURE_FILES = {
    "valid_record.json",
    "no_affiliations.json",
    "multiple_employment.json",
    "invalid_checksum.json",
}


@pytest.fixture(scope="module")
def provider() -> MockORCIDProvider:
    return MockORCIDProvider(fixture_root=FIXTURE_ROOT)


def test_mock_orcid_provider_implements_base_interface(provider: MockORCIDProvider) -> None:
    assert isinstance(provider, ORCIDProvider)
    assert not MockORCIDProvider.__abstractmethods__


def test_valid_record_returns_structured_affiliation_payload(
    provider: MockORCIDProvider,
) -> None:
    record = provider.get_person_by_orcid("0000-0002-1825-0097")

    assert record["name"] == "Alice Example"
    assert record["employment"]
    assert record["education"]
    assert record["affiliations"]


def test_record_with_no_affiliations_returns_empty_lists(
    provider: MockORCIDProvider,
) -> None:
    record = provider.get_person_by_orcid("0000-0001-3456-7898")

    assert record["employment"] == []
    assert record["education"] == []
    assert record["affiliations"] == []


def test_record_with_multiple_employment_entries_is_returned(
    provider: MockORCIDProvider,
) -> None:
    record = provider.get_person_by_orcid("0000-0003-1415-9269")

    assert len(record["employment"]) >= MIN_EMPLOYMENT_ENTRIES


def test_invalid_checksum_fixture_triggers_provider_validation_error(
    provider: MockORCIDProvider,
    load_fixture: Callable[[str, str], Any],
) -> None:
    invalid_fixture = load_fixture("providers/orcid", "invalid_checksum")

    with pytest.raises(ValueError, match="checksum"):
        provider.get_person_by_orcid(invalid_fixture["orcid_id"])


def test_orcid_format_validation_is_enforced(provider: MockORCIDProvider) -> None:
    with pytest.raises(ValueError, match="format"):
        provider.get_person_by_orcid("0000-0002-1825-009")

    with pytest.raises(ValueError, match="format"):
        provider.get_person_by_orcid("invalid-orcid")


def test_unknown_valid_orcid_raises_provider_not_found(
    provider: MockORCIDProvider,
) -> None:
    with pytest.raises(ProviderNotFoundError):
        provider.get_person_by_orcid("7913-0249-0843-820X")


def test_orcid_fixture_catalog_contains_required_files() -> None:
    fixture_files = {path.name for path in FIXTURE_ROOT.glob("*.json")}

    assert EXPECTED_FIXTURE_FILES.issubset(fixture_files)
    assert len(fixture_files) >= MIN_FIXTURE_COUNT


def test_orcid_fixture_files_are_valid_json() -> None:
    for fixture_path in sorted(FIXTURE_ROOT.glob("*.json")):
        with fixture_path.open(encoding="utf-8") as fixture_file:
            payload = json.load(fixture_file)

        assert isinstance(payload, dict)
