"""Strict validation must not strand person-dependent records in run metadata."""

from copy import deepcopy

import pytest

from git_metadata_extractor.agents.runtime import AgentRuntime
from git_metadata_extractor.pipeline.run import _assemble_output, _strict_validation
from git_metadata_extractor.pipeline.stages.jsonld_build import build_projection_input
from git_metadata_extractor.pipeline.stages.models import (
    AssembledOutput,
    ReconciledEntities,
)
from git_metadata_extractor.pipeline.stages.privacy import anonymize_email
from git_metadata_extractor.pipeline.stages.prune_dangling_refs import (
    prune_dangling_refs,
)
from git_metadata_extractor.pipeline.stages.resolve_company_to_ror import _materialise
from git_metadata_extractor.pipeline.stages.substrate import build_substrate
from git_metadata_extractor.pipeline.state import PipelineState
from git_metadata_extractor.providers.detection.github_url_classifier import (
    classify_github_url,
)
from git_metadata_extractor.schema import load_generated_context, load_jsonld_context
from git_metadata_extractor.schema.models.strict import PersonModel
from git_metadata_extractor.validation.schema_validation import StrictSchemaValidator

PERSON = "https://github.com/synthetic-person"
OTHER = "https://github.com/other-person"
ROR = "https://ror.org/02s376052"
UUID4 = "a7d4e2b1-3c8f-4a5b-9d6e-2f1a3b4c5d6e"


def _person(person_id=PERSON):
    return {
        "id": person_id, "type": "schema:Person", "shacl": "pulse:PersonShape",
        "idSource": "pulse:githubUsername", "schema:name": "Synthetic Person",
        "pulse:githubUsername": person_id,
        "identifiers": {"uuid": UUID4, "pulse:githubUsername": person_id},
    }


def _affiliation(rec, person_id):
    _materialise(
        reconciled=rec, person_id=person_id,
        hits=[{"ror_id": ROR, "name": "Example Institution"}], source="test",
        existing_org_ids={org["id"] for org in rec.entities.get("organizations", [])},
        existing_membership_keys=set(),
    )


@pytest.mark.parametrize("email", ["012345abcdef@example.space", "012345abcdef@example.technology", "1024+synthetic@users.noreply.github.com"])
def test_strict_email_and_generated_model_accept_supported_profile_addresses(email):
    person = {**_person(), "schema:email": email}
    result = StrictSchemaValidator().validate("person", person)
    assert result.is_valid, result.errors
    assert PersonModel.model_validate(person).root.schema_email == email


@pytest.mark.parametrize("email", ["missing-at.example.space", "local@@example.space", "local@example", "local@example.x", "local@exa mple.space"])
def test_strict_email_still_rejects_malformed_addresses(email):
    result = StrictSchemaValidator().validate("person", {**_person(), "schema:email": email})
    assert not result.is_valid


@pytest.mark.parametrize("exclude_person", [False, True])
def test_validation_to_substrate_keeps_only_memberships_with_live_people(load_fixture, exclude_person):
    person = {**_person(), "schema:email": anonymize_email("synthetic@example.space")}
    if exclude_person:
        person["schema:name"] = ""  # Independent strict failure must still remove dependants.
    other = _person(OTHER)
    repo = deepcopy(load_fixture("schema/strict", "pulse_RepositoryShape")[0])
    repo["id"] = repo["pulse:githubRepositoryHandle"]
    rec = ReconciledEntities(entities={"repositories": [repo], "persons": [person, other]})
    _affiliation(rec, PERSON)
    _affiliation(rec, OTHER)
    membership = f"{PERSON}__{ROR}"
    other_membership = f"{OTHER}__{ROR}"
    # Late producers maintain both collections; orphan cleanup must remove all copies.
    assert membership in {m["id"] for m in rec.entities["memberships"]}
    assert membership in {m["id"] for m in rec.memberships}
    state = PipelineState(
        run_id="email-retention", classification=classify_github_url(repo["id"]),
        runtime=AgentRuntime.RULE_BASED, providers=None, reconciled=rec,
    )
    _strict_validation(state)
    _assemble_output(state)
    assembled, warnings = prune_dangling_refs(state.assembled)
    substrate = build_substrate(
        build_projection_input(assembled=assembled, jsonld_context=load_jsonld_context()),
        run_id=state.run_id, context=load_generated_context(),
    )
    graphs = {graph["@id"]: graph["@graph"] for graph in substrate["@graph"]}
    github_ids = {node["@id"] for node in graphs["urn:pulse:output:email-retention:github"]}
    all_ids = {node["@id"] for nodes in graphs.values() for node in nodes}
    assert other_membership in github_ids
    assert (PERSON in github_ids) is not exclude_person
    assert (membership in all_ids) is not exclude_person
    if not exclude_person:
        assert membership in github_ids
        assert not assembled.excluded_entities
    else:
        assert any("Membership" in warning for warning in warnings)
    assert all(node["@type"] != "org:Membership" for node in graphs["urn:pulse:run:email-retention#meta"])


@pytest.mark.parametrize("ref_kind", ["string", "id", "@id"])
@pytest.mark.parametrize("person_is_root", [False, True])
def test_pruning_preserves_live_person_membership_refs_and_cleans_removed_refs(ref_kind, person_is_root):
    def ref(value):
        return value if ref_kind == "string" else {ref_kind: value}

    good, missing_org, orphan = "urn:membership:good", "urn:membership:missing-org", "urn:membership:orphan"
    person = {**_person(), "org:hasMembership": [ref(good), ref(missing_org)]}
    org = {"id": ROR, "type": "org:Organization", "org:hasMembership": [ref(orphan)]}
    memberships = [
        {"id": good, "type": "org:Membership", "org:organization": ROR},
        {"id": missing_org, "type": "org:Membership", "org:organization": "urn:missing"},
        {"id": orphan, "type": "org:Membership", "org:organization": ROR},
    ]
    assembled = AssembledOutput(
        root_entity=person if person_is_root else org,
        related_entities=[org if person_is_root else person, *memberships],
    )
    output, _ = prune_dangling_refs(assembled)
    nodes = [output.root_entity, *output.related_entities]
    assert {node["id"] for node in nodes} == {PERSON, ROR, good}
    assert next(node for node in nodes if node["id"] == PERSON)["org:hasMembership"] == [ref(good)]
    assert next(node for node in nodes if node["id"] == ROR)["org:hasMembership"] == []
