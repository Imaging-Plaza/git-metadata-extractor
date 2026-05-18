from __future__ import annotations

import re

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
    aliases: list[str] | None = None,
) -> dict:
    return {
        "schema:name": name,
        "aliases": aliases or [],
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
        "_person_ref": person_ref,
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


def test_reconcile_links_person_affiliations_without_generating_memberships() -> None:
    entities = {
        "persons": [_person("johndoe", affiliations=["EPFL"])],
        "organizations": [_organization("EPFL", "05gzmn429")],
        "repositories": [],
    }

    reconciled = reconcile_entities(entities)

    person = reconciled.entities["persons"][0]
    organization_id = reconciled.entities["organizations"][0]["id"]

    assert person["affiliations"] == [organization_id]
    assert reconciled.memberships == []


def test_reconcile_does_not_generate_contributions_for_person_repository_links() -> None:
    entities = {
        "persons": [_person("johndoe")],
        "organizations": [],
        "repositories": [_repository("owner/repo", ["johndoe"])],
    }

    reconciled = reconcile_entities(entities)

    assert reconciled.contributions == []


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


def test_reconcile_drops_unresolved_article_author_references() -> None:
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

    reconciled = reconcile_entities(entities)

    assert reconciled.entities["persons"] == []
    assert reconciled.entities["articles"][0]["schema:author"] == []
    orphan_warnings = [
        warning
        for warning in reconciled.link_warnings
        if "Orphan person reference from article author list" in warning
    ]
    assert len(orphan_warnings) == len(unresolved_authors)


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


def test_reconcile_does_not_add_memberships_or_contributions_without_class_entities() -> None:
    entities = {
        "persons": [_person("johndoe", affiliations=["EPFL"])],
        "organizations": [_organization("EPFL", "05gzmn429")],
        "repositories": [_repository("owner/repo", ["johndoe"])],
        "memberships": [],
        "contributions": [],
    }

    reconciled = reconcile_entities(entities)

    assert reconciled.memberships == []
    assert reconciled.contributions == []


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
        if organization["id"] == "https://github.com/sdsc-ordes"
    )

    assert "https://github.com/sdsc-ordes" in canonical_org["org:hasUnit"]
    # `org:unitOf` is a list per `pulse:OrganizationShape` (multi-valued
    # to allow joint-affiliations); reconciliation no longer collapses
    # the single-parent case to a scalar.
    assert github_org_account["org:unitOf"] == ["https://ror.org/02hdt9m26"]
    assert github_org_account["pulse:githubOrganizationHandle"] == "sdsc-ordes"
    assert reconciled.entities["repositories"][0]["pulse:ownedBy"] == "https://github.com/sdsc-ordes"


def test_reconcile_organization_ownership_is_rebuilt_from_repository_owner_links() -> None:
    owner_org = _organization(
        "Swiss Data Science Center",
        "https://ror.org/02hdt9m26",
        github_handle="sdsc-ordes",
    )
    unrelated_org = _organization(
        "University of Geneva",
        "https://ror.org/01swzsf04",
    )
    unrelated_org["pulse:owns"] = ["sdsc-ordes/gimie"]

    entities = {
        "persons": [],
        "organizations": [owner_org, unrelated_org],
        "repositories": [
            _repository(
                "sdsc-ordes/gimie",
                [],
                owned_by="sdsc-ordes",
            ),
        ],
    }

    reconciled = reconcile_entities(entities)
    organizations = {
        organization["id"]: organization
        for organization in reconciled.entities["organizations"]
    }

    assert organizations["https://ror.org/01swzsf04"]["pulse:owns"] == []
    assert organizations["https://ror.org/02hdt9m26"]["pulse:owns"] == []
    assert organizations["https://github.com/sdsc-ordes"]["pulse:owns"] == ["https://github.com/sdsc-ordes/gimie"]


def test_reconcile_resolves_accented_affiliation_variant_from_org_aliases() -> None:
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
                aliases=[
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
                aliases=["EPFL"],
            ),
        ],
        "repositories": [],
    }

    reconciled = reconcile_entities(entities)
    organization_id = reconciled.entities["organizations"][0]["id"]

    assert reconciled.entities["persons"][0]["affiliations"] == [organization_id]
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
                aliases=["SDSC-GE"],
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


def test_reconcile_strips_organization_lookup_fields_after_resolution() -> None:
    organization = _organization(
        "Swiss Data Science Center",
        "https://ror.org/02hdt9m26",
        github_handle="sdsc-ordes",
        aliases=["SDSC"],
    )
    organization["acronyms"] = ["SDSC"]
    organization["labels"] = [{"label": "Swiss Data Science Center", "iso639": "en"}]
    entities = {
        "persons": [_person("alice", affiliations=["SDSC"])],
        "organizations": [organization],
        "repositories": [],
    }

    reconciled = reconcile_entities(entities)
    canonical_org = reconciled.entities["organizations"][0]

    assert reconciled.entities["persons"][0]["affiliations"] == ["https://ror.org/02hdt9m26"]
    assert "aliases" not in canonical_org
    assert "acronyms" not in canonical_org
    assert "labels" not in canonical_org


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
    # Both `org:hasUnit` and `org:unitOf` are list-valued per the
    # OrganizationShape; reconciliation now empties the list rather than
    # collapsing it to `None`.
    assert reconciled_org["org:unitOf"] == []
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
    assert organizations["https://ror.org/04f4a0c74"]["org:unitOf"] == [
        "https://ror.org/05gzmn429",
    ]
    assert not any(
        "Dropped unresolved organization hierarchy references during reconciliation"
        in warning
        for warning in reconciled.link_warnings
    )


def test_reconcile_merges_ror_and_infoscience_variants_and_remaps_memberships() -> None:
    infoscience_uuid = "95372c6b-7d45-432e-a84e-660c9fa54e05"
    infoscience_org_id = (
        "https://infoscience.epfl.ch/server/api/core/items/"
        f"{infoscience_uuid}"
    )
    entities = {
        "persons": [_person("alice", affiliations=[infoscience_org_id])],
        "organizations": [
            _organization(
                "Swiss Data Science Center",
                "https://ror.org/02hdt9m26",
                github_handle="sdsc-ordes",
            ),
            {
                "schema:name": "Swiss Data Science Centre",
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
        "memberships": [_membership("alice", infoscience_org_id)],
    }

    reconciled = reconcile_entities(entities)
    organizations = reconciled.entities["organizations"]
    person_id = reconciled.entities["persons"][0]["id"]

    assert len(organizations) == 1
    canonical_org = organizations[0]
    assert canonical_org["id"] == "https://ror.org/02hdt9m26"
    assert canonical_org["identifiers"]["pulse:ror"] == "https://ror.org/02hdt9m26"
    assert (
        canonical_org["identifiers"]["pulse:infoscienceOrganizationIdentifier"]
        == infoscience_uuid
    )
    assert canonical_org["pulse:infoscienceOrganizationIdentifier"] == infoscience_uuid
    assert canonical_org["schema:identifier"] == "https://ror.org/02hdt9m26"
    assert reconciled.memberships == [_membership(person_id, "https://ror.org/02hdt9m26")]
    assert reconciled.reconciliation_debug["merged_group_count"] == 1
    assert reconciled.reconciliation_debug["org_remap_count"] >= 1


def test_reconcile_acronym_only_overlap_does_not_merge_and_warns_about_ambiguity() -> None:
    first = _organization(
        "Swiss Data Science Center",
        "https://ror.org/02hdt9m26",
    )
    second = _organization(
        "San Diego Supercomputer Center",
        "https://ror.org/04mg3nk07",
    )
    first["acronyms"] = ["SDSC"]
    second["acronyms"] = ["SDSC"]
    entities = {
        "persons": [],
        "organizations": [first, second],
        "repositories": [],
    }

    reconciled = reconcile_entities(entities)

    assert len(reconciled.entities["organizations"]) == 2
    assert any(
        "Ambiguous organization lookup tokens detected during reconciliation"
        in warning
        for warning in reconciled.link_warnings
    )
    assert reconciled.reconciliation_debug["merged_group_count"] == 0


def test_reconcile_token_collision_prefers_ror_backed_canonical_organization() -> None:
    infoscience_uuid = "41674f42-ba15-4612-9817-2a6f60985c01"
    ror_org = _organization(
        "Swiss Data Science Center",
        "https://ror.org/02hdt9m26",
    )
    infoscience_org = {
        "schema:name": "Some Data Systems Consortium",
        "schema:identifier": None,
        "identifiers": {
            "pulse:ror": None,
            "pulse:infoscienceOrganizationIdentifier": infoscience_uuid,
            "pulse:githubOrganizationHandle": None,
        },
        "pulse:infoscienceOrganizationIdentifier": infoscience_uuid,
        "pulse:githubOrganizationHandle": None,
        "pulse:owns": [],
    }
    ror_org["acronyms"] = ["SDSC"]
    infoscience_org["acronyms"] = ["SDSC"]

    entities = {
        "persons": [_person("alice", affiliations=["SDSC"])],
        "organizations": [ror_org, infoscience_org],
        "repositories": [],
    }

    reconciled = reconcile_entities(entities)
    person = reconciled.entities["persons"][0]

    assert len(reconciled.entities["organizations"]) == 2
    assert person["affiliations"] == ["https://ror.org/02hdt9m26"]
    assert reconciled.reconciliation_debug["token_collision_count"] >= 1


def test_reconcile_generates_missing_organization_identifier_uuid() -> None:
    entities = {
        "persons": [],
        "organizations": [
            {
                "schema:name": "Swiss Data Science Center",
                "schema:identifier": "https://ror.org/02hdt9m26",
                "identifiers": {
                    "pulse:ror": "https://ror.org/02hdt9m26",
                    "pulse:infoscienceOrganizationIdentifier": None,
                    "pulse:githubOrganizationHandle": "sdsc-ordes",
                    "uuid": None,
                },
                "pulse:githubOrganizationHandle": "sdsc-ordes",
                "pulse:infoscienceOrganizationIdentifier": None,
                "pulse:owns": [],
            },
        ],
        "repositories": [],
    }

    reconciled = reconcile_entities(entities)
    organization = reconciled.entities["organizations"][0]
    uuid_value = organization["identifiers"]["uuid"]

    assert isinstance(uuid_value, str)
    assert re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
        uuid_value,
        flags=re.IGNORECASE,
    )


def test_reconcile_derives_organization_github_handle_from_github_url_id() -> None:
    entities = {
        "persons": [],
        "organizations": [
            {
                "id": "https://github.com/sdsc-ordes",
                "idSource": "pulse:githubOrganizationHandle",
                "schema:name": "sdsc-ordes",
                "schema:identifier": None,
                "identifiers": {
                    "pulse:ror": None,
                    "pulse:infoscienceOrganizationIdentifier": None,
                    "pulse:githubOrganizationHandle": None,
                    "uuid": None,
                },
                "pulse:githubOrganizationHandle": None,
                "pulse:infoscienceOrganizationIdentifier": None,
                "pulse:owns": [],
            },
        ],
        "repositories": [],
    }

    reconciled = reconcile_entities(entities)
    organization = reconciled.entities["organizations"][0]

    assert organization["id"] == "https://github.com/sdsc-ordes"
    assert organization["idSource"] == "pulse:githubOrganizationHandle"
    assert organization["pulse:githubOrganizationHandle"] == "sdsc-ordes"
    assert organization["identifiers"]["pulse:githubOrganizationHandle"] == "sdsc-ordes"
