from __future__ import annotations

from git_metadata_extractor.pipeline.stages.link_veracity import promote_failed_id_entities
from git_metadata_extractor.pipeline.stages.models import AssembledOutput


def _person_with_orcid_and_github() -> dict:
    return {
        "id": "https://orcid.org/0000-0000-0000-0000",
        "idSource": "pulse:orcid",
        "type": "schema:Person",
        "schema:name": "Sample Person",
        "pulse:orcid": "0000-0000-0000-0000",
        "pulse:githubUsername": "sampleuser",
    }


def test_no_invalid_links_yields_no_rewrites() -> None:
    person = _person_with_orcid_and_github()
    assembled = AssembledOutput(
        root_entity=person,
        related_entities=[],
        excluded_entities=[],
        warnings=[],
    )
    promoted, rewrites, warnings = promote_failed_id_entities(
        assembled=assembled,
        invalid_links=set(),
    )
    assert rewrites == {}
    assert warnings == []
    assert promoted is assembled


def test_orcid_failed_demotes_to_github() -> None:
    person = _person_with_orcid_and_github()
    repo = {
        "id": "https://github.com/sampleuser/repo",
        "idSource": "pulse:githubRepositoryHandle",
        "type": "schema:SoftwareSourceCode",
        "schema:name": "repo",
        "pulse:githubRepositoryHandle": "sampleuser/repo",
        "schema:author": ["https://orcid.org/0000-0000-0000-0000"],
    }
    assembled = AssembledOutput(
        root_entity=repo,
        related_entities=[person],
        excluded_entities=[],
        warnings=[],
    )

    promoted, rewrites, warnings = promote_failed_id_entities(
        assembled=assembled,
        invalid_links={"https://orcid.org/0000-0000-0000-0000"},
    )

    assert rewrites == {
        "https://orcid.org/0000-0000-0000-0000": "https://github.com/sampleuser",
    }
    assert any("Promoted entity id" in w for w in warnings)
    # Person itself rewritten
    promoted_person = promoted.related_entities[0]
    assert promoted_person["id"] == "https://github.com/sampleuser"
    assert promoted_person["idSource"] == "pulse:githubUsername"
    # Reference inside the repo also rewritten
    assert promoted.root_entity["schema:author"] == ["https://github.com/sampleuser"]


def test_no_alternative_falls_back_to_uuid() -> None:
    # Person with only orcid → if orcid fails, uuid fallback
    person = {
        "id": "https://orcid.org/0000-1111-2222-3333",
        "idSource": "pulse:orcid",
        "type": "schema:Person",
        "schema:name": "Solo Orcid",
        "pulse:orcid": "0000-1111-2222-3333",
    }
    assembled = AssembledOutput(
        root_entity=None,
        related_entities=[person],
        excluded_entities=[],
        warnings=[],
    )
    promoted, rewrites, _warnings = promote_failed_id_entities(
        assembled=assembled,
        invalid_links={"https://orcid.org/0000-1111-2222-3333"},
    )
    assert "https://orcid.org/0000-1111-2222-3333" in rewrites
    new_id = rewrites["https://orcid.org/0000-1111-2222-3333"]
    assert new_id != "https://orcid.org/0000-1111-2222-3333"
    assert promoted.related_entities[0]["idSource"] == "uuid"


def test_membership_composite_id_is_left_alone() -> None:
    membership = {
        "id": "https://orcid.org/0000-0000-0000-0000_https://ror.org/01a2b3c4d",
        "idSource": "pulse:composite",
        "type": "org:Membership",
    }
    assembled = AssembledOutput(
        root_entity=None,
        related_entities=[membership],
        excluded_entities=[],
        warnings=[],
    )
    promoted, rewrites, _warnings = promote_failed_id_entities(
        assembled=assembled,
        invalid_links={
            "https://orcid.org/0000-0000-0000-0000_https://ror.org/01a2b3c4d",
        },
    )
    assert rewrites == {}
    assert promoted is assembled


def test_org_ror_failed_demotes_to_github() -> None:
    org = {
        "id": "https://ror.org/0495fxg12",
        "idSource": "pulse:ror",
        "type": "org:Organization",
        "schema:name": "EPFL",
        "pulse:ror": "0495fxg12",
        "pulse:githubOrganizationHandle": "epfl",
    }
    assembled = AssembledOutput(
        root_entity=None,
        related_entities=[org],
        excluded_entities=[],
        warnings=[],
    )
    promoted, rewrites, _warnings = promote_failed_id_entities(
        assembled=assembled,
        invalid_links={"https://ror.org/0495fxg12"},
    )
    assert rewrites == {"https://ror.org/0495fxg12": "https://github.com/epfl"}
    assert promoted.related_entities[0]["idSource"] == "pulse:githubOrganizationHandle"
