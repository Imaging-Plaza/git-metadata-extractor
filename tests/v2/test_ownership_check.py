from __future__ import annotations

from git_metadata_extractor.pipeline.stages.models import AssembledOutput, ReconciledEntities
from git_metadata_extractor.pipeline.stages.ownership_check import (
    _synthesize_owner_person_stub,
    guarantee_repo_author,
    infer_owners,
    validate_ownership,
)


def test_synthesized_owner_person_is_marked_reference_stub():
    # Bug 07: genuine placeholders must carry `_stub` so its absence reliably
    # means "independently extracted".
    person = _synthesize_owner_person_stub("octocat")
    assert person["_stub"] is True
    assert person["id"] == "https://github.com/octocat"
    assert person["type"] == "schema:Person"


def _person(*, github_username: str, owns: list) -> dict:
    return {
        "id": f"https://orcid.org/{github_username}-orcid",
        "type": "schema:Person",
        "schema:name": github_username.title(),
        "pulse:githubUsername": github_username,
        "pulse:owns": owns,
    }


def _org(*, handle: str, owns: list) -> dict:
    return {
        "id": f"https://ror.org/{handle}",
        "type": "org:Organization",
        "schema:name": handle.upper(),
        "pulse:githubOrganizationHandle": handle,
        "pulse:owns": owns,
    }


def test_owner_match_keeps_entry() -> None:
    person = _person(
        github_username="cmdoret",
        owns=[{"@id": "https://github.com/cmdoret/personal-site"}],
    )
    promoted, warnings = validate_ownership(
        AssembledOutput(root_entity=None, related_entities=[person], excluded_entities=[]),
    )
    assert warnings == []
    assert promoted.related_entities[0]["pulse:owns"] == [
        {"@id": "https://github.com/cmdoret/personal-site"},
    ]


def test_owner_mismatch_drops_entry() -> None:
    """The user's example: rmfranken should not own sdsc-ordes/repository-template."""
    person = _person(
        github_username="rmfranken",
        owns=[{"@id": "https://github.com/sdsc-ordes/repository-template"}],
    )
    promoted, warnings = validate_ownership(
        AssembledOutput(root_entity=None, related_entities=[person], excluded_entities=[]),
    )
    assert promoted.related_entities[0]["pulse:owns"] is None
    assert any(
        "sdsc-ordes/repository-template" in w and "rmfranken" in w for w in warnings
    )


def test_organization_owner_match() -> None:
    org = _org(
        handle="sdsc-ordes",
        owns=[
            {"@id": "https://github.com/sdsc-ordes/repository-template"},
            {"@id": "https://github.com/sdsc-ordes/gimie"},
        ],
    )
    promoted, warnings = validate_ownership(
        AssembledOutput(root_entity=None, related_entities=[org], excluded_entities=[]),
    )
    assert warnings == []
    assert len(promoted.related_entities[0]["pulse:owns"]) == 2


def test_partial_mismatch_keeps_only_matching_entries() -> None:
    org = _org(
        handle="sdsc-ordes",
        owns=[
            {"@id": "https://github.com/sdsc-ordes/gimie"},  # match
            {"@id": "https://github.com/some-other-org/foo"},  # mismatch
        ],
    )
    promoted, warnings = validate_ownership(
        AssembledOutput(root_entity=None, related_entities=[org], excluded_entities=[]),
    )
    kept = promoted.related_entities[0]["pulse:owns"]
    assert kept == [{"@id": "https://github.com/sdsc-ordes/gimie"}]
    assert any("some-other-org/foo" in w for w in warnings)


def test_non_github_url_passes_through() -> None:
    """Other forges (gitlab, bitbucket, ...) aren't validated yet."""
    person = _person(
        github_username="cmdoret",
        owns=[{"@id": "https://gitlab.com/somewhere/else"}],
    )
    promoted, warnings = validate_ownership(
        AssembledOutput(root_entity=None, related_entities=[person], excluded_entities=[]),
    )
    assert warnings == []
    assert promoted.related_entities[0]["pulse:owns"] == [
        {"@id": "https://gitlab.com/somewhere/else"},
    ]


def test_missing_handle_passes_through() -> None:
    """When the source entity has no GitHub handle, we can't validate."""
    person = {
        "id": "https://orcid.org/0000-0000-0000-0000",
        "type": "schema:Person",
        "schema:name": "Anon",
        "pulse:owns": [{"@id": "https://github.com/some-org/some-repo"}],
    }
    promoted, warnings = validate_ownership(
        AssembledOutput(root_entity=None, related_entities=[person], excluded_entities=[]),
    )
    assert warnings == []
    assert promoted.related_entities[0]["pulse:owns"] == [
        {"@id": "https://github.com/some-org/some-repo"},
    ]


def test_pulse_owns_on_non_person_or_org_is_stripped() -> None:
    article = {
        "id": "https://doi.org/10.1234/abc",
        "type": "schema:ScholarlyArticle",
        "schema:name": "An Article",
        "pulse:owns": [{"@id": "https://github.com/foo/bar"}],
    }
    promoted, warnings = validate_ownership(
        AssembledOutput(root_entity=None, related_entities=[article], excluded_entities=[]),
    )
    assert promoted.related_entities[0]["pulse:owns"] is None
    assert any("schema:ScholarlyArticle" in w for w in warnings)


def test_string_form_owns_entry_is_handled() -> None:
    """`pulse:owns` may be a list of strings as well as a list of dicts."""
    person = _person(
        github_username="cmdoret",
        owns=["https://github.com/cmdoret/site", "https://github.com/wrong/repo"],
    )
    promoted, warnings = validate_ownership(
        AssembledOutput(root_entity=None, related_entities=[person], excluded_entities=[]),
    )
    assert promoted.related_entities[0]["pulse:owns"] == [
        "https://github.com/cmdoret/site",
    ]
    assert any("wrong/repo" in w for w in warnings)


# -- infer_owners ----------------------------------------------------------


def _repo(*, full_name: str, owned_by=None) -> dict:
    repo: dict = {
        "id": f"https://github.com/{full_name}",
        "type": "schema:SoftwareSourceCode",
        "schema:name": full_name.split("/", maxsplit=1)[-1],
        "pulse:githubRepositoryHandle": full_name,
    }
    if owned_by is not None:
        repo["pulse:ownedBy"] = owned_by
    return repo


def test_infer_owners_links_repo_to_org() -> None:
    org = _org(handle="sdsc-ordes", owns=[])
    repo = _repo(full_name="sdsc-ordes/gimie")
    promoted, warnings = infer_owners(
        AssembledOutput(root_entity=repo, related_entities=[org], excluded_entities=[]),
    )
    assert promoted.root_entity["pulse:ownedBy"] == {"@id": "https://ror.org/sdsc-ordes"}
    assert promoted.related_entities[0]["pulse:owns"] == [
        {"@id": "https://github.com/sdsc-ordes/gimie"},
    ]
    assert any("Inferred pulse:ownedBy" in w for w in warnings)
    assert any("Inferred pulse:owns" in w for w in warnings)


def test_infer_owners_links_repo_to_person() -> None:
    person = _person(github_username="cmdoret", owns=[])
    repo = _repo(full_name="cmdoret/personal-site")
    promoted, _warnings = infer_owners(
        AssembledOutput(root_entity=repo, related_entities=[person], excluded_entities=[]),
    )
    assert promoted.root_entity["pulse:ownedBy"] == {
        "@id": "https://orcid.org/cmdoret-orcid",
    }
    assert promoted.related_entities[0]["pulse:owns"] == [
        {"@id": "https://github.com/cmdoret/personal-site"},
    ]


def test_infer_owners_no_match_leaves_repo_alone() -> None:
    org = _org(handle="some-other-org", owns=[])
    repo = _repo(full_name="sdsc-ordes/gimie")
    promoted, warnings = infer_owners(
        AssembledOutput(root_entity=repo, related_entities=[org], excluded_entities=[]),
    )
    assert promoted.root_entity.get("pulse:ownedBy") in (None, "")
    assert promoted.related_entities[0]["pulse:owns"] == []
    assert warnings == []


def test_infer_owners_does_not_overwrite_existing_owned_by() -> None:
    org = _org(handle="sdsc-ordes", owns=[])
    other_id = "https://example.org/manually-stamped"
    repo = _repo(full_name="sdsc-ordes/gimie", owned_by={"@id": other_id})
    promoted, warnings = infer_owners(
        AssembledOutput(root_entity=repo, related_entities=[org], excluded_entities=[]),
    )
    # ownedBy stays put
    assert promoted.root_entity["pulse:ownedBy"] == {"@id": other_id}
    # but the org's pulse:owns is still updated (one direction is independent
    # of the other)
    assert promoted.related_entities[0]["pulse:owns"] == [
        {"@id": "https://github.com/sdsc-ordes/gimie"},
    ]
    assert any("not overwriting" in w for w in warnings)


def test_infer_owners_dedupes_when_owns_already_contains_repo() -> None:
    repo_id = "https://github.com/sdsc-ordes/gimie"
    org = _org(handle="sdsc-ordes", owns=[{"@id": repo_id}])
    repo = _repo(full_name="sdsc-ordes/gimie")
    promoted, warnings = infer_owners(
        AssembledOutput(root_entity=repo, related_entities=[org], excluded_entities=[]),
    )
    assert promoted.related_entities[0]["pulse:owns"] == [{"@id": repo_id}]
    # only the ownedBy direction was stamped
    inferred_warnings = [w for w in warnings if "Inferred" in w]
    assert len(inferred_warnings) == 1
    assert "ownedBy" in inferred_warnings[0]


def test_infer_owners_skips_non_repository_entities() -> None:
    org = _org(handle="sdsc-ordes", owns=[])
    article = {
        "id": "https://doi.org/10.1234/abc",
        "type": "schema:ScholarlyArticle",
        "schema:name": "Some Article",
    }
    promoted, warnings = infer_owners(
        AssembledOutput(
            root_entity=None,
            related_entities=[org, article],
            excluded_entities=[],
        ),
    )
    assert warnings == []
    assert article in promoted.related_entities


def test_guarantee_repo_author_org_owner_synthesizes_distinct_person_stub() -> None:
    """When the github owner is an Organization (not a Person), `schema:author`
    can't carry the org id (SHACL requires Person). The stage must synthesize a
    Person placeholder with a non-clashing id so it survives `validate_author_classes`
    and the org entity stays untouched at its github URL.
    """
    repo = {
        "id": "https://github.com/BWHCNI/OpenMIMS",
        "type": "schema:SoftwareSourceCode",
        "schema:name": "OpenMIMS",
        "pulse:githubRepositoryHandle": "BWHCNI/OpenMIMS",
        "schema:author": [],
    }
    org = {
        "id": "https://github.com/BWHCNI",
        "type": "org:Organization",
        "schema:name": "BWHCNI",
        "pulse:githubOrganizationHandle": "BWHCNI",
    }
    reconciled = ReconciledEntities(
        entities={"repositories": [repo], "persons": [], "organizations": [org]},
    )

    new_reconciled, warnings = guarantee_repo_author(reconciled)

    repos = new_reconciled.entities["repositories"]
    persons = new_reconciled.entities["persons"]
    assert len(repos) == 1
    assert len(repos[0]["schema:author"]) == 1
    assert len(persons) == 1
    stub_id = repos[0]["schema:author"][0]
    # Stub id must NOT collide with the existing Org id at github.com/BWHCNI
    assert stub_id != "https://github.com/BWHCNI"
    assert persons[0]["id"] == stub_id
    assert persons[0]["type"] == "schema:Person"
    # Org entity should still exist and be untouched
    assert new_reconciled.entities["organizations"] == [org]
    assert any("github owner 'BWHCNI' is an Organization" in w for w in warnings)


def test_guarantee_repo_author_person_owner_uses_person_id() -> None:
    """Existing behavior: when a Person owner exists in the graph, stamp it
    directly without synthesizing a stub."""
    repo = {
        "id": "https://github.com/alice/repo",
        "type": "schema:SoftwareSourceCode",
        "pulse:githubRepositoryHandle": "alice/repo",
        "schema:author": [],
    }
    alice = {
        "id": "https://github.com/alice",
        "type": "schema:Person",
        "schema:name": "Alice",
        "pulse:githubUsername": "alice",
    }
    reconciled = ReconciledEntities(
        entities={"repositories": [repo], "persons": [alice], "organizations": []},
    )

    new_reconciled, warnings = guarantee_repo_author(reconciled)

    repos = new_reconciled.entities["repositories"]
    assert repos[0]["schema:author"] == ["https://github.com/alice"]
    # No new persons synthesized — Person owner was reused.
    assert new_reconciled.entities["persons"] == [alice]
    assert any("stamped fallback owner 'https://github.com/alice'" in w for w in warnings)


def test_entity_github_org_handle_returns_bare_handle_from_canonical_url() -> None:
    """v3.0.0: handles are stored as canonical URLs, but ROR queries +
    handle comparisons need the bare handle (the URL 500s the ROR API)."""
    from git_metadata_extractor.pipeline.stages.ownership_check import _entity_github_org_handle

    assert _entity_github_org_handle(
        {"pulse:githubOrganizationHandle": "https://github.com/epfl-lts2"},
    ) == "epfl-lts2"
    # Nested under identifiers, and bare input still resolves.
    assert _entity_github_org_handle(
        {"identifiers": {"pulse:githubOrganizationHandle": "EPFL-LTS2"}},
    ) == "epfl-lts2"
    assert _entity_github_org_handle({"schema:name": "no handle"}) is None
