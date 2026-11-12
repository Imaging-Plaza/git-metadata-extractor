"""Tests for per-source attribution — which slice each property lands in.

The substrate's unit is one platform's slice of one run, and until this module
existed an entity went into exactly one of them: `_platform_of` picked an
anchor and the whole node followed it. A person resolved from GitHub to an
ORCID iD therefore recorded the ORCID as something GitHub had said.

What is asserted here, in order of how easy each is to break:

- The **conservative** property. An entity with one source projects exactly as
  it did before the split, byte for byte. That is the guard on the corpus: a
  regression here shows up as a diff on every single-source entity rather than
  on the handful that have two.
- The **split** itself, for the two properties only a registry can have
  asserted.
- That a slice is **never identity-only**. A node carrying nothing but `@id`
  and `@type` in a named graph asserts that a source reported an entity and
  said nothing about it, which is noise the unifier would still have to
  cluster.
- That the table names only properties the **raw shapes declare**, checked
  against the real TTL rather than a copy — the same guard
  `test_raw_projection` puts on the mapping tables, for the same reason.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from rdflib import Graph

from git_metadata_extractor.pipeline.stages.raw_projection import project_raw
from git_metadata_extractor.pipeline.stages.source_attribution import (
    PROFILE_REF_PROPERTIES,
    SOURCE_BY_PROPERTY,
    split_by_source,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SHAPES = REPO_ROOT / "vendor" / "open-pulse-ontology" / "src" / "ontology"

RUN_ID = "attr-0001"


def _slices(document: dict) -> dict[str, list[dict]]:
    """Projected entity nodes by the output IRI they anchor to."""
    by_output: dict[str, list[dict]] = {}
    for node in document["@graph"]:
        anchor = node.get("pulse:partOfRun")
        if isinstance(anchor, dict) and anchor.get("@id"):
            by_output.setdefault(str(anchor["@id"]), []).append(node)
    return by_output


def _output(platform: str) -> str:
    return f"urn:pulse:output:{RUN_ID}:{platform}"


# ---------------------------------------------------------------------------
# the conservative property
# ---------------------------------------------------------------------------


def test_a_single_source_entity_is_not_split() -> None:
    """One source in, one node out — the whole corpus's normal case."""
    document = project_raw(
        [
            {
                "@id": "https://github.com/octocat/Hello-World",
                "@type": "schema:SoftwareSourceCode",
                "schema:name": "Hello-World",
                "pulse:githubRepositoryHandle": "https://github.com/octocat/Hello-World",
            },
        ],
        run_id=RUN_ID,
    )
    slices = _slices(document)
    assert list(slices) == [_output("github")]
    assert len(slices[_output("github")]) == 1
    assert slices[_output("github")][0]["schema:name"] == "Hello-World"


def test_an_entity_with_no_platform_is_neither_split_nor_anchored() -> None:
    """`_platform_of` returning None still means one unanchored node.

    A slice that cannot name its output cannot be written to a named graph, so
    splitting one would produce nodes with nowhere to go.
    """
    document = project_raw(
        [{"@id": "urn:pulse:abc", "@type": "schema:Person", "schema:name": "Nobody"}],
        run_id=RUN_ID,
    )
    persons = [n for n in document["@graph"] if n.get("@type") == "schema:Person"]
    assert len(persons) == 1
    assert "pulse:partOfRun" not in persons[0]


# ---------------------------------------------------------------------------
# the split
# ---------------------------------------------------------------------------


def test_an_orcid_on_a_github_person_lands_in_the_orcid_slice() -> None:
    document = project_raw(
        [
            {
                "@id": "https://github.com/jdoe",
                "@type": "schema:Person",
                "schema:name": "Jane Doe",
                "pulse:githubUsername": "https://github.com/jdoe",
                "pulse:orcidIdentifier": "https://orcid.org/0000-0002-1825-0097",
            },
        ],
        run_id=RUN_ID,
    )
    slices = _slices(document)
    assert set(slices) == {_output("github"), _output("orcid")}

    github = slices[_output("github")][0]
    orcid = slices[_output("orcid")][0]

    # Same entity, two sources, disjoint claims.
    assert github["@id"] == orcid["@id"] == "https://github.com/jdoe"
    assert github["schema:name"] == "Jane Doe"
    assert "pulse:orcidIdentifier" not in github
    assert orcid["pulse:orcidIdentifier"] == ["0000-0002-1825-0097"]
    assert "schema:name" not in orcid


def test_a_ror_on_a_github_organization_lands_in_the_ror_slice() -> None:
    """The case `_platform_of`'s docstring named as unanswerable.

    An organization discovered through its GitHub org and then resolved to a
    ROR: GitHub asserted the name, ROR asserted the identifier, and before the
    split both were recorded as GitHub's.
    """
    document = project_raw(
        [
            {
                "@id": "https://ror.org/02s376052",
                "@type": "org:Organization",
                "schema:name": "EPFL",
                "pulse:githubOrganizationHandle": "https://github.com/epfl",
            },
        ],
        run_id=RUN_ID,
    )
    slices = _slices(document)
    assert set(slices) == {_output("github"), _output("ror")}
    assert slices[_output("github")][0]["schema:name"] == "EPFL"
    assert slices[_output("ror")][0]["pulse:ror"] == "https://ror.org/02s376052"


def test_each_profile_reference_follows_its_own_platform() -> None:
    """Two profiles, two slices, one reference in each.

    A property-level entry could not express this: `pulse:hasProfile` is the
    same property in both cases and only the *value* says which source it
    belongs to.
    """
    document = project_raw(
        [
            {
                "@id": "https://github.com/jdoe",
                "@type": "schema:Person",
                "pulse:githubUsername": "https://github.com/jdoe",
                "pulse:infosciencePersonIdentifier": (
                    "https://infoscience.epfl.ch/person/12345"
                ),
            },
        ],
        run_id=RUN_ID,
    )
    slices = _slices(document)
    assert set(slices) == {_output("github"), _output("infoscience")}
    for platform, expected in (("github", "github"), ("infoscience", "infoscience")):
        refs = slices[_output(platform)][0]["pulse:hasProfile"]
        assert len(refs) == 1
        assert expected in refs[0]["@id"]


def test_the_anchor_slice_is_dropped_when_it_holds_nothing_of_its_own() -> None:
    """A person known to GitHub only as the holder of an ORCID.

    The GitHub slice would say "GitHub reported this person and nothing about
    them", which is not a fact worth a named graph.
    """
    entity = {
        "@id": "https://github.com/jdoe",
        "@type": "schema:Person",
        "pulse:orcidIdentifier": ["0000-0002-1825-0097"],
    }
    split = split_by_source(entity, [], anchor="pulse:GitHub")
    assert [platform for platform, _ in split] == ["pulse:ORCID"]


def test_an_identity_only_entity_survives_as_its_anchor_slice() -> None:
    """Nothing to attribute is not the same as nothing to record.

    `split_by_source` drops every identity-only slice, which for a bare node
    means dropping all of them; `raw_projection` folds that case back onto the
    anchor rather than deleting the entity from the substrate.
    """
    document = project_raw(
        [{"@id": "https://github.com/jdoe", "@type": "schema:Person"}],
        run_id=RUN_ID,
    )
    persons = [n for n in document["@graph"] if n.get("@type") == "schema:Person"]
    assert len(persons) == 1
    assert persons[0]["pulse:partOfRun"] == {"@id": _output("github")}


def test_the_anchor_slice_comes_first() -> None:
    """Order is contract: `substrate._named_subject_graph` routes dependents to
    a subject's first graph, and the anchor is the slice holding everything not
    attributed elsewhere."""
    entity = {
        "@id": "https://github.com/jdoe",
        "@type": "schema:Person",
        "schema:name": "Jane Doe",
        "pulse:orcidIdentifier": ["0000-0002-1825-0097"],
        "pulse:ror": "https://ror.org/02s376052",
    }
    split = split_by_source(entity, [], anchor="pulse:GitHub")
    assert split[0][0] == "pulse:GitHub"


# ---------------------------------------------------------------------------
# the table, against the real TTL
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def raw_shape_properties() -> set[str]:
    """Every `sh:path` the raw shapes declare, compacted to prefixed form."""
    shapes = Graph()
    for name in ("ontology-shapes-raw.ttl", "ontology-definitions-raw.ttl"):
        path = SHAPES / name
        if path.exists():
            shapes.parse(path, format="turtle")
    if not len(shapes):
        pytest.skip("ontology submodule not checked out")
    query = "SELECT DISTINCT ?p WHERE { ?shape <http://www.w3.org/ns/shacl#path> ?p }"
    return {shapes.namespace_manager.normalizeUri(row.p) for row in shapes.query(query)}


def test_every_attributed_property_is_declared_by_a_raw_shape(
    raw_shape_properties: set[str],
) -> None:
    """The table may only name properties the substrate can actually carry.

    A typo here is otherwise silent in both directions: the property never
    appears, so it is never routed, so nothing moves and nothing fails.
    """
    named = set(SOURCE_BY_PROPERTY) | set(PROFILE_REF_PROPERTIES)
    undeclared = named - raw_shape_properties
    assert not undeclared, f"not declared by any raw shape: {sorted(undeclared)}"
    # The check only means something if a wrong name would actually fail it.
    assert "pulse:notAProperty" not in raw_shape_properties


def test_every_attributed_platform_is_an_enumeration_member() -> None:
    """A platform the ontology does not declare cannot name an output.

    `ExtractionOutputShape` constrains `pulse:platform` with
    `sh:class pulse:PlatformEnumeration`, so an invented member is a violation
    on every slice it anchors.
    """
    enumerations = SHAPES / "ontology-enumerations-canonical.ttl"
    if not enumerations.exists():
        pytest.skip("ontology submodule not checked out")
    declared = Graph().parse(enumerations, format="turtle")
    members = {
        declared.namespace_manager.normalizeUri(row.m)
        for row in declared.query(
            "SELECT ?m WHERE { ?m a "
            "<https://open-pulse.epfl.ch/ontology#PlatformEnumeration> }",
        )
    }
    assert set(SOURCE_BY_PROPERTY.values()) <= members
