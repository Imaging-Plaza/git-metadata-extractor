from __future__ import annotations

from functools import lru_cache

from scripts.v2.extract_enums_from_ttl import default_ttl_path, extract_enums_from_ttl
from git_metadata_extractor.api_models.enums import DisciplineV2, OrganizationTypeV2, RepositoryTypeV2

EXPECTED_COMPUTER_SCIENCE_WIKIDATA_URI = "http://www.wikidata.org/entity/Q428691"


@lru_cache(maxsize=1)
def _ttl_enum_ids() -> dict[str, set[str]]:
    payload = extract_enums_from_ttl(default_ttl_path())
    return {
        "disciplines": {entry["id"] for entry in payload["disciplines"]},
        "repository_types": {entry["id"] for entry in payload["repository_types"]},
        "organization_types": {entry["id"] for entry in payload["organization_types"]},
    }


def test_every_ttl_discipline_has_a_matching_discipline_enum_member() -> None:
    ttl_disciplines = _ttl_enum_ids()["disciplines"]
    enum_disciplines = {discipline.value for discipline in DisciplineV2}

    assert ttl_disciplines <= enum_disciplines


def test_discipline_enum_has_no_members_missing_from_ttl() -> None:
    ttl_disciplines = _ttl_enum_ids()["disciplines"]
    enum_disciplines = {discipline.value for discipline in DisciplineV2}

    assert enum_disciplines <= ttl_disciplines


def test_repository_type_enum_matches_ttl_repository_type_enumeration() -> None:
    ttl_repository_types = _ttl_enum_ids()["repository_types"]
    enum_repository_types = {repo_type.value for repo_type in RepositoryTypeV2}

    assert enum_repository_types == ttl_repository_types


def test_organization_type_enum_matches_ttl_organization_type_enumeration() -> None:
    ttl_organization_types = _ttl_enum_ids()["organization_types"]
    enum_organization_types = {org_type.value for org_type in OrganizationTypeV2}

    assert enum_organization_types == ttl_organization_types


def test_computer_science_alias_exposes_expected_wikidata_uri() -> None:
    assert DisciplineV2.COMPUTER_SCIENCE.wikidata_uri == EXPECTED_COMPUTER_SCIENCE_WIKIDATA_URI


def test_enum_alignment_detects_ttl_drift_with_bidirectional_set_checks() -> None:
    ttl_ids = _ttl_enum_ids()
    assert len(ttl_ids["disciplines"]) == len({discipline.value for discipline in DisciplineV2})
    assert len(ttl_ids["repository_types"]) == len({repo_type.value for repo_type in RepositoryTypeV2})
    assert len(ttl_ids["organization_types"]) == len(
        {org_type.value for org_type in OrganizationTypeV2},
    )
