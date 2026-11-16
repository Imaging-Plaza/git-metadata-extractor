"""Late hybrid organization writers obey the same contract as reconciliation."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from git_metadata_extractor.agents.llm.refiners.discovery import (
    DiscoveredOrg,
    DiscoveredPerson,
)
from git_metadata_extractor.agents.llm.refiners.rescue.agent import (
    RescueDecision,
    RescueRefinerOutput,
)
from git_metadata_extractor.pipeline.stages import refine_with_llm as stage
from git_metadata_extractor.pipeline.stages.canonical_projection import (
    project_canonical,
)
from git_metadata_extractor.pipeline.stages.jsonld_build import build_projection_input
from git_metadata_extractor.pipeline.stages.models import (
    AssembledOutput,
    ReconciledEntities,
)
from git_metadata_extractor.pipeline.stages.refine_with_llm import (
    _apply_org_resolver_patch,
    _materialize_lab_org,
    _materialize_org,
    _run_rescue_pass,
)
from git_metadata_extractor.schema import load_jsonld_context
from git_metadata_extractor.validation.schema_validation import StrictSchemaValidator

UUID = "ea4d9658-25e9-4bc4-b10d-783b4a060a93"
GITHUB = "https://github.com/example-lab"


def assert_valid_profile(org):
    validation = StrictSchemaValidator().validate("organization", org)
    assert validation.is_valid, validation.errors
    assert org["pulse:githubOrganizationHandle"] == GITHUB
    assert org["identifiers"]["pulse:githubOrganizationHandle"] == GITHUB
    flat = build_projection_input(
        assembled=AssembledOutput(root_entity=org), jsonld_context=load_jsonld_context(),
    )
    graph = project_canonical(flat)["@graph"]
    profile = next(node for node in graph if node["@type"] == "pulse:OrganizationProfile")
    assert profile["pulse:organizationHandle"] == "example-lab"


@pytest.mark.parametrize("handle", ["example-lab", "@example-lab", GITHUB])
@pytest.mark.parametrize("producer", ["discovery", "lab", "resolver"])
def test_late_github_organization_survives_strict_and_projection(handle, producer):
    if producer == "discovery":
        org, warning = _materialize_org(DiscoveredOrg(
            schema_name="Example Lab", pulse_githubOrganizationHandle=handle,
            pulse_OrganizationType="pulse:ResearchInstitution", confidence=0.95,
        ), set())
        assert warning is None
    elif producer == "lab":
        org, warning = _materialize_lab_org(DiscoveredPerson(
            schema_name="Example Lab", pulse_githubUsername=handle, confidence=0.95,
        ), set(), None)
        assert warning is None
    else:
        org = {
            "id": UUID, "type": "org:Organization", "shacl": "pulse:OrganizationShape",
            "idSource": "uuid", "schema:name": "Example Lab", "identifiers": {"uuid": UUID},
        }
        assert _apply_org_resolver_patch(org, stage.OrgResolverPatch(
            pulse_githubOrganizationHandle=handle, confidence=0.95,
        ))
        assert org["id"] == UUID  # Existing inbound links must remain valid.
        assert org["identifiers"]["uuid"] == UUID
    assert_valid_profile(org)


@pytest.mark.parametrize("handle", ["https://example.org/lab", "https://github.com/lab/repo", "bad_handle"])
def test_resolver_rejects_invalid_github_identity(handle):
    org = {"id": UUID, "identifiers": {"uuid": UUID}}
    assert not _apply_org_resolver_patch(org, stage.OrgResolverPatch(
        pulse_githubOrganizationHandle=handle, confidence=0.95,
    ))
    assert org == {"id": UUID, "identifiers": {"uuid": UUID}}


@pytest.mark.parametrize("identifier", [UUID, f"https://infoscience.epfl.ch/entities/orgunit/{UUID}"])
def test_resolver_normalizes_infoscience_without_renaming_referenced_org(identifier):
    org = {
        "id": "urn:test:existing-org", "type": "org:Organization", "shacl": "pulse:OrganizationShape",
        "idSource": "uuid", "schema:name": "Example Lab", "identifiers": {"uuid": UUID},
    }
    assert _apply_org_resolver_patch(org, stage.OrgResolverPatch(
        pulse_infoscienceOrganizationIdentifier=identifier, confidence=0.95,
    ))
    assert org["id"] == "urn:test:existing-org"
    assert org["identifiers"]["uuid"] == UUID
    expected = f"https://infoscience.epfl.ch/entities/orgunit/{UUID}"
    assert org["pulse:infoscienceOrganizationIdentifier"] == expected
    assert org["identifiers"]["pulse:infoscienceOrganizationIdentifier"] == expected
    validation = StrictSchemaValidator().validate("organization", org)
    assert validation.is_valid, validation.errors


def test_rescued_github_organization_survives_strict_and_projection(monkeypatch):
    person_id = "https://github.com/example-person"
    membership_id = f"{person_id}__{GITHUB}"
    reconciled = ReconciledEntities(entities={"persons": [{
        "id": person_id, "schema:name": "Example Person",
        "_dropped_affiliations": [{"membership_id": membership_id,
                                   "org_id": GITHUB, "org_name": "Example Lab"}],
    }]})
    monkeypatch.setattr(stage.RescueRefinerAgent, "run", AsyncMock(return_value=RescueRefinerOutput(
        decisions=[RescueDecision(membership_id=membership_id, confidence=0.95)],
    )))
    _, stats = asyncio.run(_run_rescue_pass(
        reconciled=reconciled,
        gathered_context={"repository": {"full_name": "example-lab/project", "readme_content": "Example Lab"}},
    ))
    assert stats["rescued"] == 1
    assert_valid_profile(reconciled.entities["organizations"][0])
    assert reconciled.memberships[0]["org:organization"] == GITHUB
    assert reconciled.entities["persons"][0]["org:hasMembership"] == [membership_id]
