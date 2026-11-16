"""Tests for the v2 -> v3 canonical projection.

`PROVENANCE_ARCHITECTURE.md` phase 2's identity model: a Person's platform
identity becomes a `pulse:PlatformProfile` node rather than a flat
`pulse:githubUsername` string, because the store-side unifier links *profiles*
to canonical Persons and cannot do that against a string.

The v3 canonical shapes are `sh:closed`, so most of what could go wrong here is
a property that should not be present or an identity that is missing. Those are
covered by `scripts/v2/canonical_conformance.py`, which validates the
projection of every corpus result against the real
`ontology-shapes-canonical.ttl` (119/119 conformant). What is covered *here* is
the handful of decisions SHACL cannot catch — cases where a wrong answer still
validates.
"""

from __future__ import annotations

from typing import Any

import pytest

from git_metadata_extractor.pipeline.stages.canonical_projection import (
    bare_doi,
    bare_handle,
    bare_orcid,
    deposit_iri,
    profile_iri,
    project_canonical,
)

STARS = 1497
FORKS = 408


def _by_type(doc: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for node in doc["@graph"]:
        out.setdefault(str(node.get("@type")), []).append(node)
    return out


# --------------------------------------------------------------------------
# value migration — bare identifiers, except ror
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://orcid.org/0000-0002-1825-0097", "0000-0002-1825-0097"),
        ({"@id": "https://orcid.org/0000-0002-1825-0097"}, "0000-0002-1825-0097"),
        ("0000-0002-1825-0097", "0000-0002-1825-0097"),
        # The checksum digit may be X.
        ("https://orcid.org/0000-0002-1694-233X", "0000-0002-1694-233X"),
        ("https://orcid.org/not-an-orcid", None),
        (None, None),
    ],
)
def test_orcid_goes_bare(value: Any, expected: str | None) -> None:
    assert bare_orcid(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://doi.org/10.1093/mnras/stad3265", "10.1093/mnras/stad3265"),
        ("10.1093/mnras/stad3265", "10.1093/mnras/stad3265"),
        ("https://doi.org/nonsense", None),
    ],
)
def test_doi_goes_bare(value: Any, expected: str | None) -> None:
    assert bare_doi(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://github.com/octocat/Hello-World", "octocat/Hello-World"),
        ("octocat/Hello-World", "octocat/Hello-World"),
        # Host-agnostic: a self-hosted GitLab handle is still group/project.
        ("https://gitlab.epfl.ch/group/project", "group/project"),
        # Nested paths survive; the shape's pattern allows them.
        ("https://gitlab.com/a/b/c", "a/b/c"),
    ],
)
def test_repository_handle_goes_bare(value: str, expected: str) -> None:
    assert bare_handle(value) == expected


def test_ror_is_not_stripped() -> None:
    """`pulse:ror` keeps its URL form — a confirmed, deliberate asymmetry."""
    doc = project_canonical(
        [
            {
                "@id": "https://ror.org/02s376052",
                "@type": "org:Organization",
                "schema:name": "EPFL",
                "pulse:ror": "https://ror.org/02s376052",
            },
        ],
    )

    assert doc["@graph"][0]["pulse:ror"] == "https://ror.org/02s376052"


# --------------------------------------------------------------------------
# profiles
# --------------------------------------------------------------------------


def test_person_github_handle_becomes_a_platform_profile() -> None:
    doc = project_canonical(
        [
            {
                "@id": "https://github.com/octocat",
                "@type": "schema:Person",
                "schema:name": "The Octocat",
                "pulse:githubUsername": "https://github.com/octocat",
            },
        ],
    )
    nodes = _by_type(doc)
    person = nodes["schema:Person"][0]
    profile = nodes["pulse:PlatformProfile"][0]

    # The flat property is gone; PersonShape is closed and has no slot for it.
    assert "pulse:githubUsername" not in person
    assert person["pulse:hasProfile"] == [{"@id": profile["@id"]}]

    assert profile["pulse:platform"] == "pulse:GitHub"
    assert profile["pulse:platformUsername"] == ["octocat"]
    assert profile["pulse:profileOf"] == {"@id": "https://github.com/octocat"}


def test_self_hosted_instance_is_part_of_profile_identity() -> None:
    """`epfl` on gitlab.epfl.ch and on gitlab.com are two different things.

    Collapsing them is the bug ontology patch 01 exists to prevent, so the
    instance has to be in the IRI, not only in a property.
    """
    public = profile_iri("pulse:GitLab", "epfl")
    hosted = profile_iri("pulse:GitLab", "epfl", instance="https://gitlab.epfl.ch")

    assert public != hosted
    assert "gitlab.epfl.ch" in hosted


def test_person_and_organization_profiles_never_share_an_iri() -> None:
    """Regression: they did, and the second minted node replaced the first.

    This pipeline routinely produces both for one GitHub org — a Person stub
    for a repo owner and the Organization itself. With one IRI for both, the
    organization ended up pointing at a `pulse:PlatformProfile`, which
    `sh:class pulse:OrganizationProfile` rejects.
    """
    doc = project_canonical(
        [
            {
                "@id": "urn:pulse:repo-author:ANTsX/ANTsX",
                "@type": "schema:Person",
                "schema:name": "ANTsX",
                "pulse:githubUsername": "https://github.com/ANTsX",
            },
            {
                "@id": "https://github.com/ANTsX",
                "@type": "org:Organization",
                "schema:name": "ANTsX",
                "pulse:githubOrganizationHandle": "https://github.com/ANTsX",
            },
        ],
    )
    nodes = _by_type(doc)

    platform = nodes["pulse:PlatformProfile"][0]
    organization = nodes["pulse:OrganizationProfile"][0]
    assert platform["@id"] != organization["@id"]

    # And each subject links to the profile of the right class.
    person = nodes["schema:Person"][0]
    org = nodes["org:Organization"][0]
    assert person["pulse:hasProfile"] == [{"@id": platform["@id"]}]
    assert org["pulse:hasOrganizationProfile"] == [{"@id": organization["@id"]}]


def test_infoscience_handle_comes_from_the_last_path_segment() -> None:
    """`/entities/orgunit/{uuid}` — the first segment is the word "entities".

    Taking the first segment produced a profile whose handle was `"entities"`,
    which satisfies every SHACL constraint while being meaningless. No
    validation would have caught it.
    """
    uuid = "937f0b8f-dd0b-4e51-9cae-a53d1375f248"
    doc = project_canonical(
        [
            {
                "@id": f"https://infoscience.epfl.ch/entities/orgunit/{uuid}",
                "@type": "org:Organization",
                "schema:name": "Middle East EPFL Section",
                "pulse:infoscienceOrganizationIdentifier": (
                    f"https://infoscience.epfl.ch/entities/orgunit/{uuid}"
                ),
            },
        ],
    )
    profile = _by_type(doc)["pulse:OrganizationProfile"][0]

    assert profile["pulse:organizationHandle"] == uuid
    assert profile["pulse:platform"] == "pulse:Infoscience"


def test_unknown_host_yields_no_profile() -> None:
    """Guessing a platform would put a wrong value on a closed shape."""
    doc = project_canonical(
        [
            {
                "@id": "https://codeberg.org/someone",
                "@type": "schema:Person",
                "schema:name": "Someone",
                "pulse:githubUsername": "https://codeberg.org/someone",
            },
        ],
    )
    nodes = _by_type(doc)

    assert "pulse:PlatformProfile" not in nodes
    assert "pulse:hasProfile" not in nodes["schema:Person"][0]


# --------------------------------------------------------------------------
# organizations: the identity the v2 output throws away
# --------------------------------------------------------------------------


def test_ror_is_rederived_from_a_ror_shaped_id() -> None:
    """v2 strips `pulse:ror` when the id already is the ROR; v3 requires it.

    `build_jsonld_output` drops the redundant property because the v2.1.2
    Organization shape was closed against it. v3 inverts that: the identity
    `sh:or` is `pulse:ror | pulse:hasOrganizationProfile`, so the stripped
    organization has no identity at all. Every ROR org in the corpus violated
    until this was added.
    """
    doc = project_canonical(
        [
            {
                "@id": "https://ror.org/03zh00e46",
                "@type": "org:Organization",
                "schema:name": "Some Foundation",
            },
        ],
    )

    assert doc["@graph"][0]["pulse:ror"] == ["https://ror.org/03zh00e46"]


# --------------------------------------------------------------------------
# repositories are not profiles
# --------------------------------------------------------------------------


def test_repository_carries_platform_and_handle_directly() -> None:
    """`RepositoryShape` has `pulse:platform` + `pulse:repositoryHandle`.

    No profile node: unlike people and organizations, a repository's identity
    *is* its platform handle, so there is nothing to link.
    """
    doc = project_canonical(
        [
            {
                "@id": "https://github.com/ANTsX/ANTs",
                "@type": "schema:SoftwareSourceCode",
                "schema:name": "ANTs",
                "pulse:githubRepositoryHandle": "https://github.com/ANTsX/ANTs",
                "pulse:githubRepoStars": STARS,
                "pulse:githubRepoForks": FORKS,
            },
        ],
    )
    repo = doc["@graph"][0]

    assert repo["pulse:platform"] == "pulse:GitHub"
    assert repo["pulse:repositoryHandle"] == "ANTsX/ANTs"
    # Renamed to be platform-agnostic in v3.
    assert repo["pulse:repositoryStars"] == STARS
    assert repo["pulse:repositoryForks"] == FORKS
    assert "pulse:githubRepoStars" not in repo


def test_repository_platform_falls_back_to_the_id() -> None:
    """`pulse:platform` is required, and the id is a platform URL too."""
    doc = project_canonical(
        [
            {
                "@id": "https://github.com/a/b",
                "@type": "schema:SoftwareSourceCode",
                "schema:name": "b",
            },
        ],
    )

    assert doc["@graph"][0]["pulse:platform"] == "pulse:GitHub"


# --------------------------------------------------------------------------
# articles and deposits
# --------------------------------------------------------------------------


def test_article_gets_a_deposit_carrying_the_publication_date() -> None:
    """v3 puts `schema:datePublished` on the deposit, not the article.

    `ArticleShape` has no `schema:datePublished` at all, so the date is lost
    unless it moves to the platform record that actually published it.
    """
    uuid = "78b678d1-7cae-483e-b8e2-565f4bb26e7e"
    doc = project_canonical(
        [
            {
                "@id": "https://doi.org/10.1093/mnras/stad3265",
                "@type": "schema:ScholarlyArticle",
                "schema:name": "A paper",
                "schema:identifier": "https://doi.org/10.1093/mnras/stad3265",
                "schema:datePublished": "2023-12-23",
                "schema:author": [{"@id": "https://orcid.org/0000-0003-0426-6634"}],
                "pulse:infoscienceArticleIdentifier": (
                    f"https://infoscience.epfl.ch/entities/publication/{uuid}"
                ),
            },
        ],
    )
    nodes = _by_type(doc)
    article = nodes["schema:ScholarlyArticle"][0]
    deposit = nodes["pulse:Deposit"][0]

    assert article["pulse:doi"] == ["10.1093/mnras/stad3265"]
    assert "schema:datePublished" not in article
    assert article["pulse:hasDeposit"] == [{"@id": deposit["@id"]}]

    assert deposit["@id"] == deposit_iri("pulse:Infoscience", uuid)
    assert deposit["pulse:platform"] == "pulse:Infoscience"
    assert deposit["schema:datePublished"] == "2023-12-23"
    assert deposit["pulse:platformInternalId"] == uuid


def test_article_without_a_platform_record_gets_no_deposit() -> None:
    """Better no deposit than an invalid one: both its fields are required."""
    doc = project_canonical(
        [
            {
                "@id": "https://doi.org/10.1/x",
                "@type": "schema:ScholarlyArticle",
                "schema:name": "A paper",
                "schema:identifier": "https://doi.org/10.1/x",
            },
        ],
    )

    assert "pulse:Deposit" not in _by_type(doc)


# --------------------------------------------------------------------------
# hygiene
# --------------------------------------------------------------------------


def test_nulls_and_empty_collections_are_dropped() -> None:
    """v2 emits `"schema:citation": null` and `"pulse:discipline": []`.

    Both are closed-shape violations if carried through, and neither says
    anything.
    """
    doc = project_canonical(
        [
            {
                "@id": "https://github.com/a/b",
                "@type": "schema:SoftwareSourceCode",
                "schema:name": "b",
                "pulse:githubRepositoryHandle": "https://github.com/a/b",
                "schema:citation": None,
                "pulse:discipline": [],
                "schema:programmingLanguage": [],
            },
        ],
    )
    repo = doc["@graph"][0]

    assert "schema:citation" not in repo
    assert "pulse:discipline" not in repo
    assert "schema:programmingLanguage" not in repo


def test_unmapped_types_are_skipped_not_passed_through() -> None:
    """A type with no projection cannot be emitted into a closed-shape graph."""
    doc = project_canonical(
        [
            {"@id": "urn:x:1", "@type": "pulse:SomethingNew", "pulse:whatever": 1},
            {
                "@id": "https://github.com/a/b",
                "@type": "schema:SoftwareSourceCode",
                "schema:name": "b",
            },
        ],
    )

    assert [n["@id"] for n in doc["@graph"]] == ["https://github.com/a/b"]


def test_canonical_output_is_the_default_and_reversible() -> None:
    """The v3 projection *is* the output now; the flag is a rollback switch.

    Kept rather than deleted because the flip is breaking for consumers:
    `V2_CANONICAL_OUTPUT_ENABLED=false` restores the v2-shaped graph without a
    redeploy.
    """
    import os  # noqa: PLC0415

    from git_metadata_extractor.api._helpers import (  # noqa: PLC0415
        _canonical_output_enabled,
    )

    assert _canonical_output_enabled() is True

    previous = os.environ.get("V2_CANONICAL_OUTPUT_ENABLED")
    try:
        os.environ["V2_CANONICAL_OUTPUT_ENABLED"] = "false"
        assert _canonical_output_enabled() is False
    finally:
        if previous is None:
            os.environ.pop("V2_CANONICAL_OUTPUT_ENABLED", None)
        else:
            os.environ["V2_CANONICAL_OUTPUT_ENABLED"] = previous
