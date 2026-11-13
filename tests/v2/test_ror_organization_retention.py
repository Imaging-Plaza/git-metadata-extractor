"""ROR resolver output must survive the intermediate identity contract."""

from __future__ import annotations

from uuid import UUID

import pytest

from git_metadata_extractor.pipeline.stages.models import ReconciledEntities
from git_metadata_extractor.pipeline.stages.output_assembly import assemble_output
from git_metadata_extractor.pipeline.stages.prune_dangling_refs import (
    prune_dangling_refs,
)
from git_metadata_extractor.pipeline.stages.resolve_company_to_ror import (
    _build_org_stub,
    _materialise,
)
from git_metadata_extractor.pipeline.stages.resolve_placeholder_orgs_to_ror import (
    _rewrite_org_in_place,
)
from git_metadata_extractor.validation.schema_validation import StrictSchemaValidator

ROR = "https://ror.org/04qfrh333"
UUID4 = "ea4d9658-25e9-4bc4-b10d-783b4a060a93"
UUID_VERSION = 4


def test_ror_stub_satisfies_strict_identity_contract():
    org = _build_org_stub({"ror_id": ROR, "name": "Georgia Tech Research Institute"}, source="test")
    assert org is not None
    validation = StrictSchemaValidator().validate("organization", org)
    assert validation.is_valid, validation.errors
    assert org["schema:identifier"] == ROR
    assert UUID(org["identifiers"]["uuid"]).version == UUID_VERSION


@pytest.mark.parametrize("ror", [None, "", "not-a-ror", "https://example.com/04qfrh333", "https://ror.org/bad"])
def test_invalid_ror_hit_cannot_materialize_affiliation(ror):
    reconciled = ReconciledEntities()
    counts = _materialise(
        reconciled=reconciled, person_id="https://github.com/alice",
        hits=[{"ror_id": ror, "name": "Unverified"}], source="test",
        existing_org_ids=set(), existing_membership_keys=set(),
    )
    assert counts == (0, 0)
    assert not reconciled.entities.get("organizations")
    assert not reconciled.entities.get("memberships")


@pytest.mark.parametrize("existing_uuid", [UUID4, None])
def test_ror_upgrade_retains_or_supplies_required_uuid(existing_uuid):
    org = {
        "id": UUID4, "type": "org:Organization", "shacl": "pulse:OrganizationShape",
        "idSource": "uuid", "schema:name": "GTRI", "identifiers": {},
    }
    if existing_uuid:
        org["identifiers"]["uuid"] = existing_uuid
    old_id = _rewrite_org_in_place(org, ror_id=ROR, ror_name="Georgia Tech Research Institute")
    validation = StrictSchemaValidator().validate("organization", org)
    assert validation.is_valid, validation.errors
    assert old_id == UUID4
    assert org["id"] == ROR
    assert org["idSource"] == "pulse:ror"
    assert UUID(org["identifiers"]["uuid"]).version == UUID_VERSION
    if existing_uuid:
        assert org["identifiers"]["uuid"] == existing_uuid


def test_unidentified_organization_still_fails_strict_contract():
    org = {
        "id": UUID4, "type": "org:Organization", "shacl": "pulse:OrganizationShape",
        "idSource": "uuid", "schema:name": "Unknown", "identifiers": {"uuid": UUID4},
    }
    validation = StrictSchemaValidator().validate("organization", org)
    assert not validation.is_valid
    assert any(error["constraint"] == "anyOf" for error in validation.errors)


def test_resolved_affiliation_survives_validation_assembly_and_pruning():
    person_id = "https://github.com/alice"
    person = {
        "id": person_id, "type": "schema:Person", "shacl": "pulse:PersonShape",
        "idSource": "pulse:githubUsername", "schema:name": "Alice",
        "pulse:githubUsername": person_id,
        "identifiers": {"uuid": UUID4, "pulse:githubUsername": person_id},
    }
    reconciled = ReconciledEntities(entities={"persons": [person]})
    _materialise(
        reconciled=reconciled, person_id=person_id,
        hits=[{"ror_id": ROR, "name": "Georgia Tech Research Institute"}], source="test",
        existing_org_ids=set(), existing_membership_keys=set(),
    )
    reconciled.memberships = reconciled.entities["memberships"]
    reconciled.entities["persons"][0]["org:hasMembership"] = [reconciled.memberships[0]["id"]]
    payloads = [(kind, entity) for kind, bucket in reconciled.entities.items() for entity in bucket]
    batch = StrictSchemaValidator().validate_batch(payloads)
    assembled = assemble_output(reconciled, batch, root_entity_type="person")
    output, warnings = prune_dangling_refs(assembled)
    assert not output.excluded_entities
    assert not warnings
    org = next(entity for entity in output.related_entities if entity["id"] == ROR)
    assert org["schema:identifier"] == ROR
    membership = next(entity for entity in output.related_entities if entity["type"] == "org:Membership")
    assert membership["org:organization"] == ROR
    assert output.root_entity["org:hasMembership"] == [membership["id"]]
