"""Tests for the JSON-LD context generated from the SHACL shapes.

The context encodes three things the shapes already carry — `@type: @id` for
references, `@container: @set` for multi-valued properties, and `@type: xsd:*`
for typed literals — so hand-maintaining it was a standing invitation to drift.

The generated file is what `/v2/extract` serves by default: `canonical_projection`
swaps it in through `load_generated_context()`. `load_jsonld_context()` still
returns the hand-written v2 context, which stays internal and describes the
v2-shaped intermediate. The two are not interchangeable (see
`test_generated_context_is_not_a_drop_in_replacement`).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATED = REPO_ROOT / "git_metadata_extractor" / "schema" / "generated" / "context.jsonld"
HAND_WRITTEN = (
    REPO_ROOT / "git_metadata_extractor" / "schema" / "json" / "context" / "v2.0.jsonld"
)

sys.path.insert(0, str(REPO_ROOT / "scripts" / "v2"))

STARS = 42

pytestmark = pytest.mark.skipif(
    not GENERATED.exists(),
    reason="context not generated (just ontology-models-generate)",
)


@pytest.fixture
def context() -> dict:
    return json.loads(GENERATED.read_text(encoding="utf-8"))["@context"]


def test_context_is_stamped_with_the_ontology_version() -> None:
    """From `owl:versionInfo`, not the submodule SHA.

    The ontology's own statement about itself survives a re-tag or a mirror,
    and keeps the drift gate independent of git state.
    """
    payload = json.loads(GENERATED.read_text(encoding="utf-8"))

    assert payload["context_version"].startswith("v3.")
    assert "open-pulse-ontology-v3." in payload["ontology_version"]


def test_every_declared_prefix_is_in_the_context(context: dict) -> None:
    """One prefix table, shared with the reader.

    A context that cannot expand the CURIEs its own models emit is worse than
    no context, and a second hardcoded copy is how that happens.
    """
    from ontology_reader import PREFIXES  # noqa: PLC0415

    for prefix, base in PREFIXES:
        assert context.get(prefix) == base, f"{prefix} missing or wrong"


# --------------------------------------------------------------------------
# the property-level sh:or bug
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "pulse:ownedBy",
        "pulse:collectionOwnedBy",
        "pulse:collectionIncludes",
        "pulse:projectOutput",
    ],
)
def test_properties_typed_only_by_a_property_level_sh_or_are_references(
    context: dict,
    path: str,
) -> None:
    """These four carry their type in `sh:or ( [sh:class A] [sh:class B] )`.

    An earlier reader looked only at the direct `sh:datatype` / `sh:class` /
    `sh:nodeKind` predicates, so all four read as untyped and lost
    `@type: "@id"` — which makes the value expand as a plain literal and
    silently stops the edge being an edge. `pulse:ownedBy` is the one the
    pipeline actively stamps, in `infer_owners`.
    """
    assert context[path]["@type"] == "@id"


def test_owned_by_expands_to_an_edge_not_a_literal(context: dict) -> None:
    """The bug above, stated as the behaviour that actually matters."""
    from rdflib import URIRef  # noqa: PLC0415
    from rdflib.namespace import Namespace  # noqa: PLC0415

    pulse = Namespace("https://open-pulse.epfl.ch/ontology#")
    doc = {
        "@context": context,
        "@id": "https://github.com/octocat/hello",
        "pulse:ownedBy": "https://github.com/octocat",
    }
    graph = _parse(doc)

    owner = graph.value(URIRef("https://github.com/octocat/hello"), pulse.ownedBy)
    assert isinstance(owner, URIRef), f"expected an IRI, got {type(owner).__name__}"
    assert str(owner) == "https://github.com/octocat"


def _parse(doc: dict):
    from rdflib import Graph  # noqa: PLC0415

    return Graph().parse(data=json.dumps(doc), format="json-ld")


# --------------------------------------------------------------------------
# the three derivations
# --------------------------------------------------------------------------


def test_datatypes_are_carried_but_string_is_left_implicit(context: dict) -> None:
    assert context["pulse:repositoryStars"]["@type"] == "xsd:integer"
    assert context["schema:dateCreated"]["@type"] == "xsd:dateTime"
    assert context["pulse:archived"]["@type"] == "xsd:boolean"
    # `xsd:string` is JSON-LD's default for a plain literal; stating it adds
    # bytes and says nothing.
    assert "@type" not in context.get("schema:name", {})


def test_enumerated_properties_are_ids_not_strings(context: dict) -> None:
    """Enumeration members are IRIs (`pulse:Software`), so they behave as ids."""
    assert context["pulse:discipline"]["@type"] == "@id"
    assert context["pulse:repositoryType"]["@type"] == "@id"
    assert context["pulse:platform"]["@type"] == "@id"


def test_multi_valued_properties_get_a_set_container(context: dict) -> None:
    assert context["pulse:owns"]["@container"] == "@set"
    # Single-valued, so no container.
    assert "@container" not in context["pulse:ownedBy"]


def test_a_property_expands_to_a_typed_literal(context: dict) -> None:
    from rdflib import URIRef  # noqa: PLC0415
    from rdflib.namespace import XSD, Namespace  # noqa: PLC0415

    pulse = Namespace("https://open-pulse.epfl.ch/ontology#")
    graph = _parse(
        {
            "@context": context,
            "@id": "https://github.com/o/r",
            "pulse:repositoryStars": STARS,
        },
    )

    stars = graph.value(URIRef("https://github.com/o/r"), pulse.repositoryStars)
    assert stars.datatype == XSD.integer
    assert stars.toPython() == STARS


# --------------------------------------------------------------------------
# why the hand-written context stays, internally
# --------------------------------------------------------------------------


def test_generated_context_is_not_a_drop_in_replacement(context: dict) -> None:
    """Adopting it changes the wire format, so adoption is its own step.

    Two independent reasons, both asserted here so the delta is documented
    rather than discovered:

    1. v3 renamed properties. `pulse:OrganizationType` became
       `pulse:organizationType` (the capital was a v2 irregularity), and the
       github-specific counters became platform-agnostic.
    2. The accepted loose cardinality means 11 properties gain
       `@container: @set`, so a lone value serialises as a one-element array.
    """
    hand_written = json.loads(HAND_WRITTEN.read_text(encoding="utf-8"))["@context"]

    # (1) renames — the v2 term is gone, the v3 term is present.
    assert "pulse:OrganizationType" in hand_written
    assert "pulse:OrganizationType" not in context
    assert "pulse:organizationType" in context

    assert "pulse:githubRepoStars" in hand_written
    assert "pulse:githubRepoStars" not in context
    assert "pulse:repositoryStars" in context

    # (2) cardinality: scalar in v2, set in v3.
    assert "@container" not in hand_written["schema:url"]
    assert context["schema:url"]["@container"] == "@set"


def test_the_internal_context_path_is_still_the_hand_written_file() -> None:
    """Only the response moved to the generated context; this path stays v2.

    `build_jsonld_output` reads `JSONLD_CONTEXT_PATH`, through
    `load_jsonld_context`, to decide which values of the v2-shaped intermediate
    serialise as `{"@id": ...}`, and with `V2_CANONICAL_OUTPUT_ENABLED=false`
    that is the `@context` the response carries. Pointed at the generated file,
    the terms only v2 declares as references (`affiliations`,
    `pulse:OrganizationType`) lose that typing, so an organization type reads
    back as a string literal instead of an IRI. The canonical projection
    replaces both graph and context, so the default response never shows it;
    this assertion is where it surfaces.
    """
    from git_metadata_extractor.schema import JSONLD_CONTEXT_PATH  # noqa: PLC0415

    assert JSONLD_CONTEXT_PATH == HAND_WRITTEN
