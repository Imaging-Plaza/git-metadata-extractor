from __future__ import annotations

from src.v2.pipeline.stages.models import AssembledOutput
from src.v2.pipeline.stages.ownership_check import validate_ownership


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
