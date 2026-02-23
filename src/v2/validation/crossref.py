from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

MEMBERSHIP_COMPOSITE_PARTS = 2


@dataclass
class CrossRefReport:
    """Cross-reference validation summary for generated v2 entities."""

    valid_refs_count: int = 0
    invalid_refs: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)


def _entity_id(entity: dict[str, Any]) -> str | None:
    entity_id = entity.get("id")
    if isinstance(entity_id, str):
        return entity_id
    return None


def _as_ref_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _add_invalid_ref(report: CrossRefReport, issue: dict[str, Any]) -> None:
    report.invalid_refs.append(issue)


def validate_cross_references(  # noqa: C901, PLR0912, PLR0915
    entities_by_type: dict[str, Any],
) -> CrossRefReport:
    """Validate cross-entity references in a generated v2 dataset."""
    report = CrossRefReport()

    persons = entities_by_type.get("persons", [])
    repositories = entities_by_type.get("repositories", [])
    organizations = entities_by_type.get("organizations", [])
    memberships = entities_by_type.get("memberships", [])
    contributions = entities_by_type.get("contributions", [])

    person_ids = {_entity_id(person) for person in persons}
    person_ids.discard(None)
    repository_ids = {_entity_id(repo) for repo in repositories}
    repository_ids.discard(None)
    organization_ids = {_entity_id(org) for org in organizations}
    organization_ids.discard(None)
    membership_ids = {_entity_id(membership) for membership in memberships}
    membership_ids.discard(None)

    owner_claims: dict[str, set[str]] = defaultdict(set)

    # Validate direct contribution references.
    for contribution in contributions:
        contribution_id = _entity_id(contribution)

        for author_ref in _as_ref_list(contribution.get("schema:author")):
            if author_ref in person_ids:
                report.valid_refs_count += 1
            else:
                _add_invalid_ref(
                    report,
                    {
                        "type": "missing_person_reference",
                        "source_type": "contribution",
                        "source_id": contribution_id,
                        "field": "schema:author",
                        "reference": author_ref,
                        "message": (
                            "Contribution references a person ID that does not exist "
                            "in the person set."
                        ),
                    },
                )

        for repo_ref in _as_ref_list(contribution.get("pulse:contributionTo")):
            if repo_ref in repository_ids:
                report.valid_refs_count += 1
            else:
                _add_invalid_ref(
                    report,
                    {
                        "type": "missing_repository_reference",
                        "source_type": "contribution",
                        "source_id": contribution_id,
                        "field": "pulse:contributionTo",
                        "reference": repo_ref,
                        "message": (
                            "Contribution references a repository ID that does not exist "
                            "in the repository set."
                        ),
                    },
                )

    # Validate owner -> repository references and build reverse ownership index.
    for owner_type, owners in (("person", persons), ("organization", organizations)):
        for owner in owners:
            owner_id = _entity_id(owner)
            for repo_ref in _as_ref_list(owner.get("pulse:owns")):
                if repo_ref in repository_ids:
                    report.valid_refs_count += 1
                    if owner_id is not None:
                        owner_claims[repo_ref].add(owner_id)
                else:
                    _add_invalid_ref(
                        report,
                        {
                            "type": "missing_owned_repository",
                            "source_type": owner_type,
                            "source_id": owner_id,
                            "field": "pulse:owns",
                            "reference": repo_ref,
                            "message": (
                                "Owner references a repository in pulse:owns that does "
                                "not exist in the repository set."
                            ),
                        },
                    )

    # Validate repository -> owner references and symmetry with owner claims.
    owner_ids = person_ids | organization_ids
    for repository in repositories:
        repository_id = _entity_id(repository)
        owned_by = repository.get("pulse:ownedBy")
        if not isinstance(owned_by, str):
            continue

        if owned_by in owner_ids:
            report.valid_refs_count += 1
        else:
            _add_invalid_ref(
                report,
                {
                    "type": "missing_owner_reference",
                    "source_type": "repository",
                    "source_id": repository_id,
                    "field": "pulse:ownedBy",
                    "reference": owned_by,
                    "message": (
                        "Repository pulse:ownedBy references an owner that does not "
                        "exist in person or organization sets."
                    ),
                },
            )
            continue

        claiming_owner_ids = owner_claims.get(repository_id or "", set())
        if owned_by in claiming_owner_ids:
            report.valid_refs_count += 1
        else:
            _add_invalid_ref(
                report,
                {
                    "type": "bidirectional_ownership_mismatch",
                    "source_type": "repository",
                    "source_id": repository_id,
                    "field": "pulse:ownedBy",
                    "reference": owned_by,
                    "message": (
                        "Repository pulse:ownedBy is not mirrored by owner pulse:owns "
                        "references."
                    ),
                },
            )

        if len(claiming_owner_ids) > 1:
            report.warnings.append(
                {
                    "type": "multiple_owner_claims",
                    "source_type": "repository",
                    "source_id": repository_id,
                    "field": "pulse:owns",
                    "reference": repository_id,
                    "message": (
                        "Repository is claimed by multiple owners; pulse:ownedBy chooses "
                        "a single canonical owner."
                    ),
                },
            )

    # Validate membership composite IDs and organization references.
    for membership in memberships:
        membership_id = _entity_id(membership)
        if membership_id is None:
            _add_invalid_ref(
                report,
                {
                    "type": "missing_membership_id",
                    "source_type": "membership",
                    "source_id": None,
                    "field": "id",
                    "reference": "",
                    "message": "Membership entry is missing an id.",
                },
            )
            continue

        membership_parts = membership_id.split("_", maxsplit=1)
        if len(membership_parts) != MEMBERSHIP_COMPOSITE_PARTS:
            _add_invalid_ref(
                report,
                {
                    "type": "invalid_membership_composite_id",
                    "source_type": "membership",
                    "source_id": membership_id,
                    "field": "id",
                    "reference": membership_id,
                    "message": (
                        "Membership composite id must follow '<person_id>_<org_id>' "
                        "format."
                    ),
                },
            )
            continue

        person_ref, org_ref = membership_parts
        if person_ref in person_ids:
            report.valid_refs_count += 1
        else:
            _add_invalid_ref(
                report,
                {
                    "type": "membership_unknown_person",
                    "source_type": "membership",
                    "source_id": membership_id,
                    "field": "id",
                    "reference": person_ref,
                    "message": (
                        "Membership composite id references a person that does not "
                        "exist in the person set."
                    ),
                },
            )

        if org_ref in organization_ids:
            report.valid_refs_count += 1
        else:
            _add_invalid_ref(
                report,
                {
                    "type": "membership_unknown_organization",
                    "source_type": "membership",
                    "source_id": membership_id,
                    "field": "id",
                    "reference": org_ref,
                    "message": (
                        "Membership composite id references an organization that does "
                        "not exist in the organization set."
                    ),
                },
            )

        membership_org_ref = membership.get("org:organization")
        if isinstance(membership_org_ref, str) and membership_org_ref in organization_ids:
            report.valid_refs_count += 1
            if membership_org_ref == org_ref:
                report.valid_refs_count += 1
            else:
                _add_invalid_ref(
                    report,
                    {
                        "type": "membership_org_mismatch",
                        "source_type": "membership",
                        "source_id": membership_id,
                        "field": "org:organization",
                        "reference": membership_org_ref,
                        "message": (
                            "Membership org:organization must match the organization "
                            "segment in membership composite id."
                        ),
                    },
                )
        elif isinstance(membership_org_ref, str):
            _add_invalid_ref(
                report,
                {
                    "type": "membership_unknown_organization_field",
                    "source_type": "membership",
                    "source_id": membership_id,
                    "field": "org:organization",
                    "reference": membership_org_ref,
                    "message": (
                        "Membership org:organization references an organization that "
                        "does not exist."
                    ),
                },
            )

    # Validate person -> membership references.
    for person in persons:
        person_id = _entity_id(person)
        if person_id is None:
            continue
        for membership_ref in _as_ref_list(person.get("org:hasMembership")):
            if membership_ref in membership_ids:
                report.valid_refs_count += 1
                if membership_ref.startswith(f"{person_id}_"):
                    report.valid_refs_count += 1
                else:
                    _add_invalid_ref(
                        report,
                        {
                            "type": "person_membership_mismatch",
                            "source_type": "person",
                            "source_id": person_id,
                            "field": "org:hasMembership",
                            "reference": membership_ref,
                            "message": (
                                "Person membership reference points to a membership "
                                "whose composite id does not include this person id."
                            ),
                        },
                    )
            else:
                _add_invalid_ref(
                    report,
                    {
                        "type": "missing_membership_reference",
                        "source_type": "person",
                        "source_id": person_id,
                        "field": "org:hasMembership",
                        "reference": membership_ref,
                        "message": (
                            "Person org:hasMembership references a membership that does "
                            "not exist in the membership set."
                        ),
                    },
                )

    return report
