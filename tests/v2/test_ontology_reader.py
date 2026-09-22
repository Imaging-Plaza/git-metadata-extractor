"""Tests for the SHACL shape reader that drives ontology codegen.

Asserting on the extracted intermediate representation rather than on emitted
source: getting constraints out of the shapes is where the errors are, and a
dict is far easier to pin down than generated Python.

These read the real pinned ontology rather than a fixture, on purpose — the
point is to catch the submodule pin moving under us. They skip rather than fail
when the submodule is absent, so a checkout without `--recursive` does not look
like a broken test suite.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ONTOLOGY = REPO_ROOT / "vendor" / "open-pulse-ontology" / "src" / "ontology"

sys.path.insert(0, str(REPO_ROOT / "scripts" / "v2"))

pytestmark = pytest.mark.skipif(
    not ONTOLOGY.is_dir(),
    reason="ontology submodule not checked out (git submodule update --init)",
)


def _shapes(filename: str):
    from ontology_reader import read_shapes  # noqa: PLC0415

    return {shape.local_name: shape for shape in read_shapes(ONTOLOGY / filename)}


# --------------------------------------------------------------------------
# layer inventories — these fail loudly if the pin moves
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("ontology-shapes-canonical.ttl", 10),
        # 14 with local patches 06 (`RawContributionShape`) and 08
        # (`SourceSnapshotShape`); upstream has 12.
        ("ontology-shapes-raw.ttl", 14),
        ("ontology-shapes-provenance.ttl", 2),
    ],
)
def test_layer_shape_counts(filename: str, expected: int) -> None:
    assert len(_shapes(filename)) == expected


def test_canonical_layer_contains_the_expected_entity_shapes() -> None:
    assert set(_shapes("ontology-shapes-canonical.ttl")) == {
        "ArticleShape",
        "ContributionShape",
        "DepositShape",
        "MembershipShape",
        "OrganizationProfileShape",
        "OrganizationShape",
        "PersonShape",
        "PlatformProfileShape",
        "ProjectShape",
        "RepositoryShape",
    }


# --------------------------------------------------------------------------
# constraint extraction
# --------------------------------------------------------------------------


def test_person_shape_constraints() -> None:
    person = _shapes("ontology-shapes-canonical.ttl")["PersonShape"]

    assert person.target_class == "schema:Person"
    assert person.closed is True
    assert "owl:sameAs" in person.ignored_properties

    by_path = {p.path: p for p in person.properties}

    # Required, literal-valued.
    assert by_path["schema:name"].required
    assert by_path["schema:name"].datatype == "xsd:string"

    # Reference-valued: a class, not a literal.
    profile = by_path["pulse:hasProfile"]
    assert profile.node_class == "pulse:PlatformProfile"
    assert profile.is_reference
    assert not profile.required

    # Pattern-constrained.
    assert by_path["pulse:orcidIdentifier"].pattern is not None


def test_identity_alternatives_are_read_from_sh_or() -> None:
    """`sh:or` encodes "ORCID or at least one profile" — the ID hierarchy."""
    person = _shapes("ontology-shapes-canonical.ttl")["PersonShape"]

    flattened = {path for alt in person.identity_alternatives for path in alt}
    assert flattened == {"pulse:orcidIdentifier", "pulse:hasProfile"}


def test_organization_identity_is_ror_or_a_profile() -> None:
    org = _shapes("ontology-shapes-canonical.ttl")["OrganizationShape"]

    flattened = {path for alt in org.identity_alternatives for path in alt}
    assert flattened == {"pulse:ror", "pulse:hasOrganizationProfile"}


def test_inline_and_named_property_shapes_are_both_read() -> None:
    """`sh:property pulse:NameShape` and `sh:property [ ... ]` must both work."""
    org = _shapes("ontology-shapes-canonical.ttl")["OrganizationShape"]
    by_path = {p.path: p for p in org.properties}

    # `pulse:ror` comes from the named `pulse:RorShape`.
    assert by_path["pulse:ror"].pattern is not None
    # `pulse:owns` is declared inline as a blank node.
    assert by_path["pulse:owns"].node_class == "schema:SoftwareSourceCode"


def test_cardinality_distinguishes_single_from_multi_valued() -> None:
    profile = _shapes("ontology-shapes-canonical.ttl")["OrganizationProfileShape"]
    by_path = {p.path: p for p in profile.properties}

    platform = by_path["pulse:platform"]
    assert platform.required
    assert platform.single_valued

    # Display name is unconstrained in count.
    assert not by_path["schema:name"].single_valued


def test_property_level_sh_or_yields_class_alternatives() -> None:
    """`pulse:ownedBy` is typed by `sh:or ( [sh:class A] [sh:class B] )`.

    Two different `sh:or` shapes appear in these files and they mean opposite
    things: at node level it says which properties *identify* a node
    (`identity_alternatives`), at property level it says which types a *value*
    may have. Reading only the direct `sh:class` predicate leaves these four
    properties looking untyped, which costs them `@type: "@id"` in the JSON-LD
    context and turns a graph edge into a string.
    """
    repo = _shapes("ontology-shapes-canonical.ttl")["RepositoryShape"]
    owned_by = next(p for p in repo.properties if p.path == "pulse:ownedBy")

    assert owned_by.class_alternatives == ["schema:Person", "org:Organization"]
    assert owned_by.is_reference
    assert owned_by.node_class is None  # no *direct* class — that is the point
    assert owned_by.single_valued


def test_directly_typed_properties_have_no_class_alternatives() -> None:
    org = _shapes("ontology-shapes-canonical.ttl")["OrganizationShape"]
    owns = next(p for p in org.properties if p.path == "pulse:owns")

    assert owns.class_alternatives == []
    assert owns.node_class == "schema:SoftwareSourceCode"
    assert owns.is_reference


def test_ontology_version_is_readable() -> None:
    """Stamped into the generated context instead of a git SHA."""
    from ontology_reader import read_version  # noqa: PLC0415

    version = read_version(ONTOLOGY / "ontology-definitions-canonical.ttl")

    assert version is not None
    assert version.startswith("v3.")


# --------------------------------------------------------------------------
# the local patch series
# --------------------------------------------------------------------------


def test_platform_instance_patch_is_applied() -> None:
    """`ontology/patches/01` must reach all four profile shapes.

    Fails when the ontology has not been prepared — run `just ontology-prepare`.
    Once the patch is upstreamed and deleted, this should still pass.
    """
    canonical = _shapes("ontology-shapes-canonical.ttl")
    raw = _shapes("ontology-shapes-raw.ttl")

    for shapes, names in (
        (canonical, ("PlatformProfileShape", "OrganizationProfileShape")),
        (raw, ("RawPlatformProfileShape", "RawOrganizationProfileShape")),
    ):
        for name in names:
            paths = {p.path for p in shapes[name].properties}
            assert "pulse:platformInstance" in paths, f"{name} lacks platformInstance"


def test_platform_instance_is_a_single_iri() -> None:
    profile = _shapes("ontology-shapes-canonical.ttl")["PlatformProfileShape"]
    instance = next(
        p for p in profile.properties if p.path == "pulse:platformInstance"
    )

    assert instance.node_kind == "IRI"
    assert instance.single_valued
    assert not instance.required  # absent means the public instance
