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


def _organization(
    name: str,
    ror: str,
    *,
    github_handle: str | None = None,
    alternate_names: list[str] | None = None,
) -> dict:
    return {
        "schema:name": name,
        "schema:alternateName": alternate_names or [],
        "schema:identifier": ror,
        "identifiers": {
            "pulse:ror": ror,
            "pulse:infoscienceOrganizationIdentifier": None,
            "pulse:githubOrganizationHandle": github_handle,
        },
        "pulse:githubOrganizationHandle": github_handle,
        "pulse:owns": [],
    }


def _repository(
    handle: str,
    authors: list[str],
    *,
    fork_of: str | None = None,
    owned_by: str | None = None,
) -> dict:
    return {
        "schema:name": handle,
        "pulse:githubRepositoryHandle": handle,
        "identifiers": {
            "pulse:githubRepositoryHandle": handle,
            "schema:citation": None,
        },
        "schema:author": authors,
        "pulse:isForkOf": fork_of,
        "pulse:ownedBy": owned_by,
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


def _membership(person_ref: str, org_ref: str) -> dict:
    membership_id = f"{person_ref}_{org_ref}"
    return {
        "id": membership_id,
        "type": "org:Membership",
        "shacl": "pulse:MembershipShape",
        "identifiers": {"pulse:composite": membership_id, "uuid": "11111111-1111-4111-8111-111111111111"},
        "idSource": "pulse:composite",
        "org:organization": org_ref,
        "org:role": "Researcher",
        "time:hasBeginning": "2021-01-01",
        "time:hasEnd": None,
    }


def _contribution(person_ref: str, repository_ref: str) -> dict:
    contribution_id = f"{person_ref}_{repository_ref}"
    return {
        "id": contribution_id,
        "type": "pulse:Contribution",
        "shacl": "pulse:ContributionShape",
        "identifiers": {"pulse:composite": contribution_id, "uuid": "22222222-2222-4222-8222-222222222222"},
        "idSource": "pulse:composite",
        "pulse:contributionTo": repository_ref,
        "pulse:contributionCount": 7,
        "pulse:firstContributionDate": "2020-01-01T00:00:00Z",
        "pulse:lastContributionDate": "2025-01-01T00:00:00Z",
        "schema:author": person_ref,
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


def test_reconcile_drops_repository_author_references_that_match_organizations() -> None:
    entities = {
        "persons": [_person("johndoe")],
        "organizations": [
            _organization(
                "Swiss Data Science Center",
                "https://ror.org/02hdt9m26",
                github_handle="sdsc-ordes",
            ),
        ],
        "repositories": [_repository("owner/repo", ["johndoe", "sdsc-ordes"])],
    }

    reconciled = reconcile_entities(entities)
    person_id = reconciled.entities["persons"][0]["id"]

    assert reconciled.entities["repositories"][0]["schema:author"] == [person_id]
    assert not any(
        "author=sdsc-ordes" in warning
        for warning in reconciled.link_warnings
    )


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
    assert article["idSource"] == "pulse:infoscienceArticleIdentifier"
    assert article["schema:author"] == [person_id]
    assert article["schema:sourceOrganization"] == organization_id


def test_reconcile_resolves_article_author_when_article_references_person_orcid_token() -> None:
    person = _person("johndoe")
    person["pulse:orcidIdentifier"] = "0000-0002-1825-0097"
    person["identifiers"]["pulse:orcid"] = "0000-0002-1825-0097"
    entities = {
        "persons": [person],
        "organizations": [],
        "repositories": [],
        "articles": [
            _article(
                doi="10.1000/linked-author",
                infoscience_id=None,
                authors=["0000-0002-1825-0097"],
                source_organization=None,
            ),
        ],
    }

    reconciled = reconcile_entities(entities)

    article = reconciled.entities["articles"][0]
    person_id = reconciled.entities["persons"][0]["id"]

    assert person_id == "https://orcid.org/0000-0002-1825-0097"
    assert article["schema:author"] == [person_id]
    assert not any(
        "Dropped unresolved article author reference because synthetic fallbacks are disabled"
        in warning
        for warning in reconciled.link_warnings
    )


def test_reconcile_normalizes_infoscience_organization_identifier_url_to_uuid() -> None:
    infoscience_uuid = "95372c6b-7d45-432e-a84e-660c9fa54e05"
    infoscience_url = (
        "https://infoscience.epfl.ch/server/api/entities/organization/"
        f"{infoscience_uuid}/full"
    )
    entities = {
        "persons": [],
        "organizations": [
            {
                "schema:name": "EPFL Unit",
                "schema:identifier": None,
                "identifiers": {
                    "pulse:ror": None,
                    "pulse:infoscienceOrganizationIdentifier": infoscience_url,
                    "pulse:githubOrganizationHandle": None,
                },
                "pulse:infoscienceOrganizationIdentifier": infoscience_url,
                "pulse:githubOrganizationHandle": None,
                "pulse:owns": [],
            },
        ],
        "repositories": [],
    }

    reconciled = reconcile_entities(entities)
    organization = reconciled.entities["organizations"][0]

    assert organization["pulse:infoscienceOrganizationIdentifier"] == infoscience_uuid
    assert organization["identifiers"]["pulse:infoscienceOrganizationIdentifier"] == infoscience_uuid
    assert organization["id"] == f"https://infoscience.epfl.ch/server/api/core/items/{infoscience_uuid}"
    assert organization["idSource"] == "pulse:infoscienceOrganizationIdentifier"


def test_reconcile_preserves_uuid_infoscience_organization_identifier() -> None:
    infoscience_uuid = "41674f42-ba15-4612-9817-2a6f60985c01"
    entities = {
        "persons": [],
        "organizations": [
            {
                "schema:name": "Another EPFL Unit",
                "schema:identifier": None,
                "identifiers": {
                    "pulse:ror": None,
                    "pulse:infoscienceOrganizationIdentifier": infoscience_uuid,
                    "pulse:githubOrganizationHandle": None,
                },
                "pulse:infoscienceOrganizationIdentifier": infoscience_uuid,
                "pulse:githubOrganizationHandle": None,
                "pulse:owns": [],
            },
        ],
        "repositories": [],
    }

    reconciled = reconcile_entities(entities)
    organization = reconciled.entities["organizations"][0]

    assert organization["pulse:infoscienceOrganizationIdentifier"] == infoscience_uuid
    assert organization["identifiers"]["pulse:infoscienceOrganizationIdentifier"] == infoscience_uuid
    assert organization["id"] == f"https://infoscience.epfl.ch/server/api/core/items/{infoscience_uuid}"
    assert organization["idSource"] == "pulse:infoscienceOrganizationIdentifier"


def test_reconcile_synthesizes_person_for_unresolved_article_author_references() -> None:
    unresolved_authors = ["Gehant, Sebastien", "Gfeller, David"]
    entities = {
        "persons": [],
        "organizations": [],
        "repositories": [],
        "articles": [
            _article(
                doi="10.1093/nar/gkv1310",
                infoscience_id=None,
                authors=unresolved_authors,
            ),
        ],
    }

    reconciled = reconcile_entities(entities, allow_synthetic_fallbacks=True)

    article = reconciled.entities["articles"][0]
    synthesized_people = reconciled.entities["persons"]
    person_ids = {person["id"] for person in synthesized_people}
    person_names = {person["schema:name"] for person in synthesized_people}

    assert person_names == set(unresolved_authors)
    assert len(person_ids) == len(unresolved_authors)
    assert set(article["schema:author"]) == person_ids
    assert all(not person_id.startswith("https://github.com/") for person_id in person_ids)
    assert all(person["idSource"] == "uuid" for person in synthesized_people)
    assert not any(
        "Orphan person reference from article author list" in warning
        for warning in reconciled.link_warnings
    )
    assert any(
        "Synthesized fallback person entity for unresolved article author" in warning
        for warning in reconciled.synthesis_warnings
    )


def test_reconcile_does_not_synthesize_person_for_unresolved_article_authors_when_disabled() -> None:
    unresolved_authors = ["Gehant, Sebastien", "Gfeller, David"]
    entities = {
        "persons": [],
        "organizations": [],
        "repositories": [],
        "articles": [
            _article(
                doi="10.1093/nar/gkv1310",
                infoscience_id=None,
                authors=unresolved_authors,
            ),
        ],
    }

    reconciled = reconcile_entities(entities, allow_synthetic_fallbacks=False)

    assert reconciled.entities["persons"] == []
    assert reconciled.entities["articles"][0]["schema:author"] == []
    assert reconciled.synthesis_warnings == []
    assert any(
        "Dropped unresolved article author reference because synthetic fallbacks are disabled"
        in warning
        for warning in reconciled.link_warnings
    )


def test_reconcile_uses_class_memberships_and_contributions_as_primary_sources() -> None:
    entities = {
        "persons": [_person("johndoe", affiliations=["EPFL"])],
        "organizations": [_organization("EPFL", "05gzmn429")],
        "repositories": [_repository("owner/repo", ["johndoe"])],
        "memberships": [_membership("johndoe", "EPFL")],
        "contributions": [_contribution("johndoe", "owner/repo")],
    }

    reconciled = reconcile_entities(entities)

    person_id = reconciled.entities["persons"][0]["id"]
    organization_id = reconciled.entities["organizations"][0]["id"]
    repository_id = reconciled.entities["repositories"][0]["id"]

    assert reconciled.memberships == [
        _membership(person_id, organization_id),
    ]
    assert reconciled.contributions == [
        _contribution(person_id, repository_id),
    ]
    assert reconciled.synthesis_warnings == []


def test_reconcile_synthesizes_fallback_links_only_when_class_entities_missing() -> None:
    entities = {
        "persons": [_person("johndoe", affiliations=["EPFL"])],
        "organizations": [_organization("EPFL", "05gzmn429")],
        "repositories": [_repository("owner/repo", ["johndoe"])],
        "memberships": [],
        "contributions": [],
    }

    reconciled = reconcile_entities(entities)

    assert len(reconciled.memberships) == 1
    assert len(reconciled.contributions) == 1
    assert any("Synthesized fallback membership entity" in w for w in reconciled.synthesis_warnings)
    assert any("Synthesized fallback contribution entity" in w for w in reconciled.synthesis_warnings)


def test_reconcile_skips_fallback_memberships_and_contributions_when_disabled() -> None:
    entities = {
        "persons": [_person("johndoe", affiliations=["EPFL"])],
        "organizations": [_organization("EPFL", "05gzmn429")],
        "repositories": [_repository("owner/repo", ["johndoe"])],
        "memberships": [],
        "contributions": [],
    }

    reconciled = reconcile_entities(entities, allow_synthetic_fallbacks=False)

    assert reconciled.memberships == []
    assert reconciled.contributions == []
    assert reconciled.synthesis_warnings == []
    assert any(
        "Skipped fallback membership synthesis because synthetic fallbacks are disabled"
        in warning
        for warning in reconciled.link_warnings
    )
    assert any(
        "Skipped fallback contribution synthesis because synthetic fallbacks are disabled"
        in warning
        for warning in reconciled.link_warnings
    )


def test_reconcile_models_github_org_account_as_unit_for_repository_owner() -> None:
    entities = {
        "persons": [],
        "organizations": [
            _organization(
                "Swiss Data Science Center",
                "https://ror.org/02hdt9m26",
                github_handle="sdsc-ordes",
            ),
        ],
        "repositories": [
            _repository(
                "sdsc-ordes/gimie",
                [],
                owned_by="sdsc-ordes",
            ),
        ],
    }

    reconciled = reconcile_entities(entities)

    organizations = reconciled.entities["organizations"]
    canonical_org = next(
        organization
        for organization in organizations
        if organization["id"] == "https://ror.org/02hdt9m26"
    )
    github_org_account = next(
        organization
        for organization in organizations
        if organization["id"] == "sdsc-ordes"
    )

    assert "sdsc-ordes" in canonical_org["org:hasUnit"]
    assert github_org_account["org:unitOf"] == "https://ror.org/02hdt9m26"
    assert github_org_account["pulse:githubOrganizationHandle"] == "sdsc-ordes"
    assert reconciled.entities["repositories"][0]["pulse:ownedBy"] == "sdsc-ordes"


def test_reconcile_resolves_accented_affiliation_variant_from_org_alternate_names() -> None:
    entities = {
        "persons": [
            _person(
                "johndoe",
                affiliations=["EPFL - École Polytechnique Fédérale de Lausanne"],
            ),
        ],
        "organizations": [
            _organization(
                "Ecole Polytechnique Federale de Lausanne",
                "https://ror.org/02s376052",
                alternate_names=[
                    "EPFL",
                    "EPFL - Ecole Polytechnique Federale de Lausanne",
                ],
            ),
        ],
        "repositories": [],
    }

    reconciled = reconcile_entities(entities)
    organization = reconciled.entities["organizations"][0]
    organization_id = organization["id"]

    assert reconciled.entities["persons"][0]["affiliations"] == [organization_id]
    assert reconciled.memberships[0]["org:organization"] == organization_id
    assert "schema:alternateName" not in organization
    assert not any(
        "Orphan organization reference from person affiliation" in warning
        for warning in reconciled.link_warnings
    )


def test_reconcile_resolves_affiliation_with_prefixed_org_alias_without_explicit_alternate_name() -> None:
    entities = {
        "persons": [
            _person(
                "johndoe",
                affiliations=["EPFL - École Polytechnique Fédérale de Lausanne"],
            ),
        ],
        "organizations": [
            _organization(
                "École Polytechnique Fédérale de Lausanne",
                "https://ror.org/02s376052",
                alternate_names=["EPFL"],
            ),
        ],
        "repositories": [],
    }

    reconciled = reconcile_entities(entities)
    organization_id = reconciled.entities["organizations"][0]["id"]

    assert reconciled.entities["persons"][0]["affiliations"] == [organization_id]
    assert reconciled.memberships[0]["org:organization"] == organization_id
    assert not any(
        "Orphan organization reference from person affiliation" in warning
        for warning in reconciled.link_warnings
    )


def test_reconcile_resolves_membership_org_aliases_and_handle_variants() -> None:
    entities = {
        "persons": [_person("johndoe", affiliations=["SDSC-GE", "@SwissDataScienceCenter"])],
        "organizations": [
            _organization(
                "Swiss Data Science Center",
                "https://ror.org/02hdt9m26",
                github_handle="SwissDataScienceCenter",
                alternate_names=["SDSC-GE"],
            ),
        ],
        "repositories": [],
        "memberships": [_membership("johndoe", "@SwissDataScienceCenter")],
    }

    reconciled = reconcile_entities(entities)
    person_id = reconciled.entities["persons"][0]["id"]
    organization_id = reconciled.entities["organizations"][0]["id"]

    assert reconciled.entities["persons"][0]["affiliations"] == [organization_id]
    assert reconciled.memberships == [_membership(person_id, organization_id)]
    assert not any(
        "Unresolved class membership reference during reconciliation" in warning
        for warning in reconciled.link_warnings
    )


def test_reconcile_prunes_unresolved_organization_hierarchy_links() -> None:
    organization = _organization(
        "Swiss Data Science Center",
        "https://ror.org/02hdt9m26",
    )
    organization["org:hasUnit"] = ["https://ror.org/999999999"]
    organization["org:unitOf"] = "https://ror.org/888888888"
    entities = {
        "persons": [],
        "organizations": [organization],
        "repositories": [],
    }

    reconciled = reconcile_entities(entities)
    reconciled_org = reconciled.entities["organizations"][0]

    assert reconciled_org["org:hasUnit"] == []
    assert reconciled_org["org:unitOf"] is None
    assert any(
        "Dropped unresolved organization hierarchy references during reconciliation: "
        "org:hasUnit=1, org:unitOf=1"
        in warning
        for warning in reconciled.link_warnings
    )


def test_reconcile_preserves_resolvable_organization_hierarchy_links() -> None:
    parent = _organization(
        "Parent Organization",
        "https://ror.org/05gzmn429",
    )
    child = _organization(
        "Child Organization",
        "https://ror.org/04f4a0c74",
    )
    parent["org:hasUnit"] = ["https://ror.org/04f4a0c74"]
    child["org:unitOf"] = "https://ror.org/05gzmn429"
    entities = {
        "persons": [],
        "organizations": [parent, child],
        "repositories": [],
    }

    reconciled = reconcile_entities(entities)
    organizations = {organization["id"]: organization for organization in reconciled.entities["organizations"]}

    assert organizations["https://ror.org/05gzmn429"]["org:hasUnit"] == ["https://ror.org/04f4a0c74"]
    assert organizations["https://ror.org/04f4a0c74"]["org:unitOf"] == "https://ror.org/05gzmn429"
    assert not any(
        "Dropped unresolved organization hierarchy references during reconciliation"
        in warning
        for warning in reconciled.link_warnings
    )
