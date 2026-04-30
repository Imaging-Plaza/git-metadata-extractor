from __future__ import annotations

from src.v2.pipeline.stages.models import AssembledOutput
from src.v2.pipeline.stages.ownership_check import infer_owners, validate_ownership


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
