from __future__ import annotations

from src.v2.testing.mock_generator import generate_dataset
from src.v2.quality.crossref import CrossRefReport, validate_cross_references


def _remove_repo_from_claiming_owner(dataset: dict[str, list[dict]], repo_id: str) -> None:
    for person in dataset["persons"]:
        owns = person.get("pulse:owns", [])
        if isinstance(owns, list) and repo_id in owns:
            owns.remove(repo_id)
            return
    for organization in dataset["organizations"]:
        owns = organization.get("pulse:owns", [])
        if isinstance(owns, list) and repo_id in owns:
            owns.remove(repo_id)
            return


def test_generated_seed_dataset_passes_all_cross_reference_checks() -> None:
    dataset = generate_dataset(seed=42)

    report = validate_cross_references(dataset)

    assert isinstance(report, CrossRefReport)
    assert report.valid_refs_count > 0
    assert report.invalid_refs == []


def test_orphaned_contribution_person_reference_is_detected() -> None:
    dataset = generate_dataset(seed=42)
    dataset["contributions"][0]["schema:author"] = "missing-person-id"

    report = validate_cross_references(dataset)

    assert any(
        issue["type"] == "missing_person_reference"
        and issue["field"] == "schema:author"
        for issue in report.invalid_refs
    )


def test_bidirectional_ownership_mismatch_is_detected() -> None:
    dataset = generate_dataset(seed=42)
    target_repo = dataset["repositories"][0]
    repo_id = target_repo["id"]
    _remove_repo_from_claiming_owner(dataset, repo_id)

    report = validate_cross_references(dataset)

    assert any(
        issue["type"] == "bidirectional_ownership_mismatch"
        and issue["source_id"] == repo_id
        for issue in report.invalid_refs
    )


def test_membership_composite_id_must_resolve_existing_person_and_org() -> None:
    dataset = generate_dataset(seed=42)
    dataset["memberships"][0]["id"] = "missing-person_missing-org"
    dataset["memberships"][0]["_person_ref"] = "missing-person"
    dataset["memberships"][0]["org:organization"] = "missing-org"

    report = validate_cross_references(dataset)

    issue_types = {issue["type"] for issue in report.invalid_refs}
    assert "membership_unknown_person" in issue_types
    assert "membership_unknown_organization" in issue_types


def test_crossref_report_exposes_required_fields() -> None:
    report = validate_cross_references({})

    assert hasattr(report, "valid_refs_count")
    assert hasattr(report, "invalid_refs")
    assert hasattr(report, "warnings")
    assert isinstance(report.invalid_refs, list)
    assert isinstance(report.warnings, list)
