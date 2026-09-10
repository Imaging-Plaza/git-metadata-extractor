"""Tests for the validation split: which shapes go with which layer.

`PROVENANCE_ARCHITECTURE.md` phase 6. Three layers with three different
contracts, and the pairing is the thing that has to be right — because getting
it wrong is silent in both directions. Validate the substrate against the
canonical shapes and the raw layer's deliberate openness reads as violations;
validate canonical against the raw shapes and almost nothing is checked at all.

The severities differ for a reason worth restating, since it looks inconsistent
from outside: the substrate **reports** and the canonical graph **refuses**.
The substrate is append-only and durable, so refusing to write a slice over one
malformed entity loses the rest of it permanently. The canonical graph is a
pure function of the substrate, so refusing costs one re-run and leaves the
previous valid graph answering queries.

`graph:prov` has no tests here because it has no shapes and can never have
any: pyshacl cannot target a quoted triple as a focus node.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from git_metadata_extractor.validation import (
    CanonicalValidationError,
    Layer,
    enforce_canonical,
    validate_layer,
    validate_substrate,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ONTOLOGY = REPO_ROOT / "vendor" / "open-pulse-ontology" / "src" / "ontology"
CONTEXT_FILE = (
    REPO_ROOT / "git_metadata_extractor" / "schema" / "generated" / "context.jsonld"
)

pytestmark = pytest.mark.skipif(
    not ONTOLOGY.is_dir(),
    reason="ontology submodule not checked out (git submodule update --init)",
)

ORCID = "0000-0002-1825-0097"
ORCID_IRI = f"https://orcid.org/{ORCID}"
CANONICAL_GRAPH = "urn:pulse:graph:canonical"
OUTPUT_GRAPH = "urn:pulse:output:run-a:github"


def _context() -> dict[str, Any]:
    return json.loads(CONTEXT_FILE.read_text(encoding="utf-8"))["@context"]


def _named(graph: str, nodes: list[dict[str, Any]]) -> dict[str, Any]:
    return {"@context": _context(), "@graph": [{"@id": graph, "@graph": nodes}]}


def _person(**extra: Any) -> dict[str, Any]:
    return {
        "@id": ORCID_IRI,
        "@type": "schema:Person",
        "schema:name": ["Jane Doe"],
        "pulse:orcidIdentifier": [ORCID],
        **extra,
    }


# --------------------------------------------------------------------------
# the canonical gate refuses
# --------------------------------------------------------------------------


def test_a_conforming_canonical_graph_passes() -> None:
    result = enforce_canonical(_named(CANONICAL_GRAPH, [_person()]))

    assert result.conforms
    assert result.layer is Layer.CANONICAL
    assert result.triples > 0


def test_a_closed_shape_violation_refuses_publication() -> None:
    """`PersonShape` is `sh:closed`, so an undeclared property is a violation.

    `pulse:samePersonAs` is the real case: it is a *subproperty* of
    `owl:sameAs`, and the shape ignores only `owl:sameAs` itself. It belongs in
    `graph:prov` (see `unify/provenance.py`), and this is the gate that would
    have caught it being emitted here.
    """
    document = _named(
        CANONICAL_GRAPH,
        [_person(**{"pulse:samePersonAs": [{"@id": "https://github.com/jane"}]})],
    )

    with pytest.raises(CanonicalValidationError) as caught:
        enforce_canonical(document)

    assert caught.value.violations
    assert "not published" in str(caught.value)


def test_the_error_names_some_violations_and_counts_the_rest() -> None:
    """The caller has just refused to publish; it needs to say why."""
    document = _named(
        CANONICAL_GRAPH,
        [
            _person(**{"pulse:bogusOne": ["x"], "pulse:bogusTwo": ["y"]}),
            {
                "@id": "https://ror.org/02s376052",
                "@type": "org:Organization",
                "schema:name": ["EPFL"],
                "pulse:ror": ["https://ror.org/02s376052"],
                "pulse:bogusThree": ["z"],
            },
        ],
    )

    with pytest.raises(CanonicalValidationError) as caught:
        enforce_canonical(document)

    message = str(caught.value)
    assert "SHACL violation" in message
    assert len(caught.value.violations) >= 3  # noqa: PLR2004 — three bogus properties


def test_validate_layer_never_raises_even_when_the_canonical_gate_would() -> None:
    """The reporting entry point is separate from the enforcing one.

    Deliberately two functions rather than a boolean: a caller that wants a
    report and a caller that wants a gate are asking different questions, and
    a flag makes it easy to pass the wrong one.
    """
    document = _named(CANONICAL_GRAPH, [_person(**{"pulse:bogus": ["x"]})])

    result = validate_layer(document, Layer.CANONICAL, quads=True)

    assert result.conforms is False
    assert result.violations


# --------------------------------------------------------------------------
# the substrate reports
# --------------------------------------------------------------------------


def test_the_substrate_accepts_what_canonical_would_reject() -> None:
    """The raw layer is open on purpose, and that is the whole split.

    `RawPersonShape` is `sh:closed false`, so a source may assert something
    the canonical layer has no slot for — a follower count, a biography — and
    the substrate has to keep it. Validating the substrate against the
    canonical shapes would report every one of those as a violation.
    """
    nodes = [
        {
            "@id": ORCID_IRI,
            "@type": "schema:Person",
            "schema:name": ["Jane Doe"],
            "pulse:orcidIdentifier": [ORCID],
            "pulse:partOfRun": {"@id": OUTPUT_GRAPH},
        },
        # The anchor's target has to be *in* the graph. `pulse:partOfRun`
        # carries `sh:class pulse:ExtractionOutput`, so a reference alone is a
        # violation rather than a forward declaration — the same rule that
        # made the substrate writer carry its run descriptor (§3g). The first
        # version of this fixture omitted it and the substrate correctly
        # refused.
        {
            "@id": OUTPUT_GRAPH,
            "@type": "pulse:ExtractionOutput",
            "pulse:platform": "pulse:GitHub",
        },
    ]

    substrate = validate_substrate(_named(OUTPUT_GRAPH, nodes))
    canonical = validate_layer(_named(OUTPUT_GRAPH, nodes), Layer.CANONICAL, quads=True)

    assert substrate.conforms is True
    # `pulse:partOfRun` is a substrate property; the closed canonical
    # PersonShape rejects it. Same nodes, opposite verdicts — which is why the
    # pairing has to be explicit.
    assert canonical.conforms is False


def test_a_malformed_substrate_node_is_reported_not_refused() -> None:
    """`RawPlatformProfileShape` *is* closed, so the raw layer can fail.

    When it does, the answer is a report: the slice still contains every other
    entity the run found, and the substrate is the only durable copy of them.
    """
    document = _named(
        OUTPUT_GRAPH,
        [
            {
                "@id": "urn:pulse:profile:github:jane",
                "@type": "pulse:PlatformProfile",
                "pulse:platform": "pulse:GitHub",
                "pulse:notAThing": ["x"],
            },
        ],
    )

    result = validate_substrate(document)

    assert result.conforms is False
    assert result.violations
    assert result.layer is Layer.SUBSTRATE


def test_the_substrate_is_validated_as_a_union_of_its_named_graphs() -> None:
    """SHACL has no notion of a graph name, so the union is the target.

    Which is also why a node in the *wrong* named graph conforms perfectly
    (§3i) and needs the separate stranded-entity check.
    """
    document = {
        "@context": _context(),
        "@graph": [
            {"@id": OUTPUT_GRAPH, "@graph": [_person()]},
            {
                "@id": "urn:pulse:output:run-a:ror",
                "@graph": [
                    {
                        "@id": "https://ror.org/02s376052",
                        "@type": "org:Organization",
                        "schema:name": ["EPFL"],
                        "pulse:ror": ["https://ror.org/02s376052"],
                    },
                ],
            },
        ],
    }

    result = validate_substrate(document)

    assert result.conforms is True
    # Both graphs' triples were seen, not just the first.
    assert result.triples > len(_person())


# --------------------------------------------------------------------------
# a skip is not a pass
# --------------------------------------------------------------------------


def test_a_skipped_gate_is_distinguishable_from_a_clean_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing submodule must not look like a green run.

    `conforms=True` with `skipped=True` is the only honest encoding: the gate
    cannot report a violation it never looked for, and a caller that treats
    the two the same will ship an unvalidated graph believing otherwise.
    """
    monkeypatch.setattr(
        "git_metadata_extractor.validation.layers.canonical_shapes_available",
        lambda: False,
    )

    result = enforce_canonical(_named(CANONICAL_GRAPH, [_person(**{"pulse:bogus": ["x"]})]))

    assert result.skipped is True
    assert result.conforms is True
    assert "not prepared" in (result.reason or "")
    assert "skipped" in result.summary()
