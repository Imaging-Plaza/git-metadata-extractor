from __future__ import annotations

from src.v2.pipeline.stages.reconciliation import reconcile_entities


def _person(github_username: str, *, affiliations: list[str] | None = None) -> dict:
    return {
        "schema:name": github_username,
        "pulse:githubUsername": github_username,
        "identifiers": {
            "pulse:orcid": None,
            "pulse:infosciencePersonIdentifier": None,
            "pulse:githubUsername": github_username,
        },
        "affiliations": affiliations or [],
    }


def _organization(name: str, ror: str) -> dict:
    return {
        "schema:name": name,
        "schema:identifier": ror,
        "identifiers": {
            "pulse:ror": ror,
            "pulse:infoscienceOrganizationIdentifier": None,
            "pulse:githubOrganizationHandle": None,
        },
        "pulse:owns": [],
    }


def _repository(handle: str, authors: list[str], *, fork_of: str | None = None) -> dict:
    return {
        "schema:name": handle,
        "pulse:githubRepositoryHandle": handle,
        "identifiers": {
            "pulse:githubRepositoryHandle": handle,
            "schema:identifier": None,
        },
        "schema:author": authors,
        "pulse:isForkOf": fork_of,
    }


def _article(
    *,
    doi: str | None,
    infoscience_id: str | None,
    authors: list[str],
    source_organization: str | None = None,
) -> dict:
    return {
        "schema:name": "Sample Article",
        "schema:datePublished": "2025-01-01",
        "identifiers": {
            "schema:identifier": doi,
            "pulse:infoscienceArticleIdentifier": infoscience_id,
            "uuid": "5a2ad6f9-0fcf-4fc4-bfc8-7f8c8924eca5",
        },
        "schema:identifier": doi,
        "pulse:infoscienceArticleIdentifier": infoscience_id,
        "schema:author": authors,
        "schema:sourceOrganization": source_organization,
    }


def test_reconcile_updates_repository_author_references_to_canonical_person_ids() -> None:
    entities = {
        "persons": [_person("johndoe")],
        "organizations": [],
        "repositories": [_repository("owner/repo", ["johndoe"])],
    }

    reconciled = reconcile_entities(entities)

    person_id = reconciled.entities["persons"][0]["id"]
    assert reconciled.entities["repositories"][0]["schema:author"] == [person_id]


def test_reconcile_links_person_affiliations_and_generates_memberships() -> None:
    entities = {
        "persons": [_person("johndoe", affiliations=["EPFL"])],
        "organizations": [_organization("EPFL", "05gzmn429")],
        "repositories": [],
    }

    reconciled = reconcile_entities(entities)

    person = reconciled.entities["persons"][0]
    organization_id = reconciled.entities["organizations"][0]["id"]

    assert person["affiliations"] == [organization_id]
    assert len(reconciled.memberships) == 1
    assert reconciled.memberships[0]["org:organization"] == organization_id


def test_reconcile_generates_contributions_for_person_repository_links() -> None:
    entities = {
        "persons": [_person("johndoe")],
        "organizations": [],
        "repositories": [_repository("owner/repo", ["johndoe"])],
    }

    reconciled = reconcile_entities(entities)

    assert len(reconciled.contributions) == 1
    contribution = reconciled.contributions[0]
    assert contribution["schema:author"] == reconciled.entities["persons"][0]["id"]
    assert contribution["pulse:contributionTo"] == reconciled.entities["repositories"][0]["id"]


def test_reconcile_emits_warnings_for_orphan_references() -> None:
    entities = {
        "persons": [_person("johndoe", affiliations=["unknown-org"])],
        "organizations": [],
        "repositories": [_repository("owner/repo", ["missing-person"])],
    }

    reconciled = reconcile_entities(entities)

    assert reconciled.link_warnings
    assert any("Orphan person reference" in warning for warning in reconciled.link_warnings)
    assert any("Orphan organization reference" in warning for warning in reconciled.link_warnings)


def test_reconcile_detects_circular_repository_references_with_warning() -> None:
    entities = {
        "persons": [],
        "organizations": [],
        "repositories": [
            _repository("owner/repo-a", [], fork_of="owner/repo-b"),
            _repository("owner/repo-b", [], fork_of="owner/repo-a"),
        ],
    }

    reconciled = reconcile_entities(entities)

    assert any("Circular repository fork reference detected" in w for w in reconciled.link_warnings)


def test_reconcile_normalizes_article_ids_and_article_relationship_references() -> None:
    entities = {
        "persons": [_person("johndoe")],
        "organizations": [_organization("EPFL", "05gzmn429")],
        "repositories": [],
        "articles": [
            _article(
                doi=None,
                infoscience_id=(
                    "https://infoscience.epfl.ch/server/api/entities/publication/"
                    "dbce93b0-4ad7-45f2-8a53-b85bf39aeec9/full"
                ),
                authors=["johndoe"],
                source_organization="EPFL",
            ),
        ],
    }

    reconciled = reconcile_entities(entities)

    article = reconciled.entities["articles"][0]
    person_id = reconciled.entities["persons"][0]["id"]
    organization_id = reconciled.entities["organizations"][0]["id"]

    assert article["id"] == (
        "https://infoscience.epfl.ch/server/api/core/items/"
        "dbce93b0-4ad7-45f2-8a53-b85bf39aeec9"
    )
    assert article["idSource"] == "infoscienceArticleIdentifier"
    assert article["schema:author"] == [person_id]
    assert article["schema:sourceOrganization"] == organization_id
