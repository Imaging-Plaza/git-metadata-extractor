"""Late affiliation producers must preserve the Person -> Membership edge."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

import pytest

from git_metadata_extractor.agents.llm.refiners.bio_resolver import BioResolverPatch
from git_metadata_extractor.agents.runtime import AgentRuntime
from git_metadata_extractor.pipeline.run import _assemble_output, _strict_validation
from git_metadata_extractor.pipeline.stages.jsonld_build import build_projection_input
from git_metadata_extractor.pipeline.stages.models import ReconciledEntities
from git_metadata_extractor.pipeline.stages.resolve_bio_to_ror import (
    run_resolve_bio_to_ror_stage,
)
from git_metadata_extractor.pipeline.stages.resolve_bio_to_ror_llm import (
    _apply_patch,
    run_resolve_bio_to_ror_llm_stage,
)
from git_metadata_extractor.pipeline.stages.resolve_company_to_ror import (
    _existing_membership_keys,
    _existing_org_ids,
    _materialise,
    run_resolve_company_to_ror_stage,
)
from git_metadata_extractor.pipeline.stages.substrate import build_substrate
from git_metadata_extractor.pipeline.state import PipelineState
from git_metadata_extractor.providers.detection.github_url_classifier import (
    classify_github_url,
)
from git_metadata_extractor.schema import load_generated_context, load_jsonld_context

PERSON = "https://github.com/alice"
ROR = "https://ror.org/02s376052"
MEMBERSHIP = "https://github.com/alice__https://ror.org/02s376052"


class _Ror:
    async def search(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return [{"ror_id": ROR, "name": "EPFL", "score": 0.95, "types": ["education"]}]


class _GitHub:
    def get_user(self, login: str) -> dict[str, Any]:
        return {"login": login, "name": "EPFL", "type": "Organization"}


def _reconciled() -> ReconciledEntities:
    return ReconciledEntities(entities={"persons": [{
        "id": PERSON,
        "type": "schema:Person",
        "shacl": "pulse:PersonShape",
        "identifiers": {
            "pulse:orcid": None,
            "pulse:infosciencePersonIdentifier": None,
            "pulse:githubUsername": PERSON,
            "uuid": "a7d4e2b1-3c8f-4a5b-9d6e-2f1a3b4c5d6e",
        },
        "idSource": "pulse:githubUsername",
        "schema:name": "Alice",
        "pulse:githubUsername": PERSON,
        "org:hasMembership": [],
    }]})


def _materialize(rec: ReconciledEntities, *, person_id: str = PERSON, ror: str = ROR):
    return _materialise(
        reconciled=rec, person_id=person_id,
        hits=[{"ror_id": ror, "name": "EPFL"}], source="resolve_company_to_ror",
        existing_org_ids=_existing_org_ids(rec),
        existing_membership_keys=_existing_membership_keys(rec),
    )


def _apply(rec: ReconciledEntities, person: dict[str, Any], *, ror: str = ROR):
    return _apply_patch(
        person=person, patch=BioResolverPatch(pulse_ror=ror, reason="at EPFL", confidence=0.95),
        reconciled=rec, existing_org_ids=_existing_org_ids(rec),
        existing_membership_keys=_existing_membership_keys(rec),
    )


@pytest.mark.parametrize("producer", ["company", "bio", "bio_llm", "github"])
def test_late_membership_reaches_subject_graph_after_validation_and_assembly(producer):
    rec = _reconciled()
    person = rec.entities["persons"][0]
    expected = MEMBERSHIP
    if producer == "bio_llm":
        _apply(rec, person)
    elif producer == "bio":
        person["_bio"] = "Researcher at EPFL"
        asyncio.run(run_resolve_bio_to_ror_stage(reconciled=rec, provider=_Ror()))
    else:
        person["_company"] = "@epfl" if producer == "github" else "EPFL"
        asyncio.run(run_resolve_company_to_ror_stage(
            reconciled=rec, provider=_Ror(),
            github_provider=_GitHub() if producer == "github" else None,
        ))
        if producer == "github":
            expected = "https://github.com/alice__https://github.com/epfl"

    assert [m["id"] for m in rec.entities["memberships"]] == [expected]
    state = PipelineState(
        run_id="membership-link", classification=classify_github_url(PERSON),
        runtime=AgentRuntime.RULE_BASED, providers=None, reconciled=rec,
    )
    _strict_validation(state)
    assert not state.artifacts.strict_batch.invalid_entities
    assert any(
        kind == "membership" and entity["id"] == expected
        for kind, entity in state.artifacts.strict_batch.valid_entities
    )
    _assemble_output(state)
    assert not state.assembled.excluded_entities
    substrate = build_substrate(
        build_projection_input(assembled=state.assembled, jsonld_context=load_jsonld_context()),
        run_id=state.run_id, context=load_generated_context(),
    )
    graphs = {g["@id"]: g["@graph"] for g in substrate["@graph"]}
    github_nodes = graphs["urn:pulse:output:membership-link:github"]
    assert expected in {n["@id"] for n in github_nodes}
    assert all(n["@type"] != "org:Membership" for n in graphs["urn:pulse:run:membership-link#meta"])
    assert person["org:hasMembership"] == [expected]


@pytest.mark.parametrize("producer", ["company", "bio_llm"])
@pytest.mark.parametrize("previous_links", [[], ["urn:pulse:other-membership"], [MEMBERSHIP]])
def test_accepted_existing_membership_repairs_inverse_once(producer, previous_links):
    rec = _reconciled()
    _materialize(rec)
    person = rec.entities["persons"][0]
    person["org:hasMembership"] = list(previous_links)
    membership_before = deepcopy(rec.entities["memberships"])
    for _ in range(2):
        counts = _materialize(rec) if producer == "company" else _apply(rec, person)
        assert counts == (0, 0)
    assert person["org:hasMembership"].count(MEMBERSHIP) == 1
    assert set(previous_links) <= set(person["org:hasMembership"])
    assert rec.entities["memberships"] == membership_before
    assert [m["id"] for m in rec.memberships] == [MEMBERSHIP]


@pytest.mark.parametrize("producer", ["company", "bio_llm"])
def test_missing_person_does_not_create_orphan_membership(producer):
    rec = _reconciled()
    before = rec.model_dump()
    counts = (
        _materialize(rec, person_id="https://github.com/missing") if producer == "company"
        else _apply(rec, {"id": "https://github.com/missing"})
    )
    assert counts == (0, 0)
    assert rec.model_dump() == before


def test_duplicate_hits_and_reruns_preserve_one_membership_and_link():
    rec = _reconciled()
    person = rec.entities["persons"][0]
    person.pop("org:hasMembership")
    for _ in range(2):
        _materialise(
            reconciled=rec, person_id=PERSON,
            hits=[{"ror_id": ROR, "name": "EPFL"}] * 2, source="resolve_company_to_ror",
            existing_org_ids=_existing_org_ids(rec),
            existing_membership_keys=_existing_membership_keys(rec),
        )
    assert person["org:hasMembership"] == [MEMBERSHIP]
    assert [m["id"] for m in rec.entities["memberships"]] == [MEMBERSHIP]
    assert [m["id"] for m in rec.memberships] == [MEMBERSHIP]


def test_existing_github_membership_repairs_missing_inverse_on_rerun():
    rec = _reconciled()
    person = rec.entities["persons"][0]
    person["_company"] = "@epfl"
    expected = "https://github.com/alice__https://github.com/epfl"
    for _ in range(2):
        person["org:hasMembership"] = []
        asyncio.run(run_resolve_company_to_ror_stage(
            reconciled=rec, provider=_Ror(), github_provider=_GitHub(),
        ))
        assert person["org:hasMembership"] == [expected]
    assert [m["id"] for m in rec.entities["memberships"]] == [expected]
    assert [m["id"] for m in rec.memberships] == [expected]


@pytest.mark.parametrize("producer", ["company", "bio_llm", "github"])
@pytest.mark.parametrize("divergent_bucket", [False, True])
def test_reconciled_membership_evidence_survives_late_resolution(producer, divergent_bucket):
    rec = _reconciled()
    person = rec.entities["persons"][0]
    person_id = "https://orcid.org/0000-0001-2345-6789"
    person.update({"id": person_id, "idSource": "pulse:orcid"})
    person["identifiers"]["pulse:orcid"] = person_id
    person["pulse:orcidIdentifier"] = person_id
    org_id = "https://github.com/epfl" if producer == "github" else ROR
    membership_id = f"{person_id}__{org_id}"
    original = {
        "id": membership_id, "type": "org:Membership", "shacl": "pulse:MembershipShape",
        "identifiers": {
            "pulse:composite": membership_id,
            "uuid": "b3f2a1c4-5d6e-4b7a-8c9d-1e2f3a4b5c6d",
        },
        "idSource": "pulse:composite", "org:organization": org_id,
        "org:role": "Researcher", "time:hasBeginning": "2020-01-01",
        "time:hasEnd": "2024-06-30", "_source": "orcid", "_person_ref": person_id,
    }
    rec.memberships = [deepcopy(original)]
    if divergent_bucket:
        rec.entities["memberships"] = [{
            **deepcopy(original), "_source": "resolve_company_to_ror", "org:role": None,
        }]
    for _ in range(2):
        if producer == "bio_llm":
            memberships_added, _ = _apply(rec, person)
        else:
            person["_company"] = "@epfl" if producer == "github" else "EPFL"
            result = asyncio.run(run_resolve_company_to_ror_stage(
                reconciled=rec, provider=_Ror(),
                github_provider=_GitHub() if producer == "github" else None,
            ))
            memberships_added = result.memberships_created
        assert memberships_added == 0
        assert rec.memberships == [original]
        assert rec.entities["memberships"] == [original]
        assert person["org:hasMembership"] == [membership_id]

    state = PipelineState(
        run_id="existing-membership", classification=classify_github_url(PERSON),
        runtime=AgentRuntime.RULE_BASED, providers=None, reconciled=rec,
    )
    _strict_validation(state)
    assert not state.artifacts.strict_batch.invalid_entities
    _assemble_output(state)
    assembled_memberships = [
        entity for entity in state.assembled.related_entities if entity["type"] == "org:Membership"
    ]
    assert assembled_memberships == [original]


@pytest.mark.parametrize("producer", ["bio", "bio_llm"])
def test_bio_stages_skip_person_with_membership_only_in_reconciled_collection(producer):
    rec = _reconciled()
    _materialize(rec)
    rec.entities.pop("memberships")
    rec.entities["persons"][0]["_bio"] = "Researcher at EPFL"
    if producer == "bio":
        result = asyncio.run(run_resolve_bio_to_ror_stage(reconciled=rec, provider=_Ror()))
        assert result.queries_attempted == 0
    else:
        # A candidate would reach the provider-unavailable branch and warn.
        result = asyncio.run(run_resolve_bio_to_ror_llm_stage(reconciled=rec))
        assert result.persons_called == 0
        assert result.warnings == []
    assert result.memberships_created == 0
    assert "memberships" not in rec.entities


@pytest.mark.parametrize("producer", ["company", "bio_llm"])
@pytest.mark.parametrize("existing_org", [False, True])
def test_malformed_ror_never_creates_membership_or_inverse(producer, existing_org):
    rec = _reconciled()
    malformed = "https://ror.org/invalid"
    if existing_org:
        rec.entities["organizations"] = [{"id": malformed}]
    person = rec.entities["persons"][0]
    counts = (
        _materialize(rec, ror=malformed) if producer == "company"
        else _apply(rec, person, ror=malformed)
    )
    assert counts == (0, 0)
    assert not rec.entities.get("memberships")
    assert person["org:hasMembership"] == []
