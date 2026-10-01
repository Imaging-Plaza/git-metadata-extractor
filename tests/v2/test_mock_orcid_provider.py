from __future__ import annotations

from pathlib import Path

import pytest

from git_metadata_extractor.providers.base import ProviderNotFoundError
from git_metadata_extractor.providers.mock_orcid import MockORCIDProvider

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "providers" / "orcid"
MIN_EMPLOYMENT_ENTRIES = 2


@pytest.fixture(scope="module")
def provider() -> MockORCIDProvider:
    return MockORCIDProvider(fixture_root=FIXTURE_ROOT)


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


def test_invalid_checksum_triggers_provider_validation_error(
    provider: MockORCIDProvider,
) -> None:
    # Well-formed, but the check digit of 0000-0002-1825-0097 is 7, not 8.
    with pytest.raises(ValueError, match="checksum"):
        provider.get_person_by_orcid("0000-0002-1825-0098")


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
