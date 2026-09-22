"""Validate a layer document against the shape set that describes it.

`PROVENANCE_ARCHITECTURE.md` phase 6: the validation split. Three layers, three
different contracts, and one place that knows which is which — because getting
the pairing wrong is silent. Validating the substrate against the canonical
shapes reports the raw layer's deliberate openness as violations; validating
canonical against the raw shapes reports nothing at all, because the raw
entity shapes require almost nothing.

| Layer | Shapes | Closed? | On violation |
|---|---|---|---|
| substrate | `ontology-shapes-raw.ttl` + friends | mostly open | **report** |
| canonical | `ontology-shapes-canonical.ttl` + friends | closed | **refuse to publish** |
| provenance | — | — | cannot be validated at all |

**Why the two severities differ.** The substrate is append-only and is the
durable layer: refusing to write a slice because one entity is malformed loses
the other entities in it permanently, and the raw shapes are open precisely so
a source can say something unexpected. The canonical graph is the opposite — a
pure function of the substrate, rewritten on every pass, and the one consumers
query. Publishing an invalid canonical graph is worse than publishing none,
because the previous valid one is still there and still answers.

**`graph:prov` is structurally unvalidatable.** pyshacl 0.28.1 cannot target a
quoted triple as a focus node, so no shape can ever apply to an RDF-star
annotation. That is not a gap to close later — `ontology-shapes-provenance.ttl`
says so in its own header, and it is why the provenance graph is not one of the
shape sets.

**Conformance is not sufficiency**, and this module can only check the first.
The substrate passed raw conformance 119/119 while missing two things the
canonical layer requires (§3j of `REFACTOR_HANDOFF.md`) — a per-layer gate
cannot see that, because each layer was individually well-formed. The check
that catches it is building canonical *from* the substrate and validating the
result, which is what the enforcing gate in `unify.runner` does.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from rdflib import RDF, Dataset, Graph

from git_metadata_extractor.validation.ontology import (
    canonical_shapes_available,
    load_canonical_shapes_graph,
    load_raw_shapes_graph,
    raw_shapes_available,
)
from git_metadata_extractor.validation.shacl_validation import (
    SHACLRuntimeUnavailableError,
    SHACLValidator,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)


class Layer(Enum):
    """A layer of the four-layer model that has a shape set."""

    SUBSTRATE = "substrate"
    CANONICAL = "canonical"


#: How many violations the error message names before summarising the rest.
_MESSAGE_SAMPLE = 3


class CanonicalValidationError(RuntimeError):
    """The canonical graph does not conform, so it was not published.

    Carries the violations rather than only a count: the caller is a writer
    that just refused to publish, and the operator needs to know what to fix.
    """

    def __init__(self, violations: list[dict[str, Any]]) -> None:
        summary = "; ".join(
            str(item.get("message") or item.get("path") or "?")[:120]
            for item in violations[:_MESSAGE_SAMPLE]
        )
        extra = (
            f" (+{len(violations) - _MESSAGE_SAMPLE} more)"
            if len(violations) > _MESSAGE_SAMPLE
            else ""
        )
        super().__init__(
            f"canonical graph has {len(violations)} SHACL violation(s), not "
            f"published: {summary}{extra}",
        )
        self.violations = violations


@dataclass(slots=True)
class LayerValidationResult:
    layer: Layer
    conforms: bool
    violations: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    triples: int = 0
    #: True when the shapes were not available, so nothing was checked. Not
    #: the same as conforming, and the caller must be able to tell them apart:
    #: a missing submodule looks exactly like a clean run otherwise.
    skipped: bool = False
    reason: str | None = None

    def summary(self) -> str:
        if self.skipped:
            return f"{self.layer.value}: skipped ({self.reason})"
        return (
            f"{self.layer.value}: conforms={self.conforms} "
            f"violations={len(self.violations)} triples={self.triples}"
        )


def _shapes_for(layer: Layer) -> tuple[bool, Graph | None]:
    """(are the shapes available, the shapes graph) for one layer.

    Dispatched here rather than through a module-level table of callables: a
    table captures the function objects at import, so a caller — a test, most
    obviously — cannot substitute `canonical_shapes_available` by patching the
    module namespace. Resolving at call time keeps the seam where it is
    expected to be.
    """
    if layer is Layer.SUBSTRATE:
        return raw_shapes_available(), (
            load_raw_shapes_graph() if raw_shapes_available() else None
        )
    return canonical_shapes_available(), (
        load_canonical_shapes_graph() if canonical_shapes_available() else None
    )


#: The graph naming the outputs and the run every slice points at. Matched by
#: suffix because the run id varies; `substrate.META_GRAPH_SUFFIX` is the same
#: constant, not imported here to keep `validation` free of a pipeline import.
_META_GRAPH_SUFFIX = "#meta"


def _graph_from_document(document: Mapping[str, Any], *, quads: bool) -> Graph:
    """Parse a JSON-LD layer document into a single graph.

    `quads=False` only: a canonical document is one graph. The substrate goes
    through `substrate_slices`, which keeps the names.
    """
    payload = json.dumps(dict(document))
    if not quads:
        return Graph().parse(data=payload, format="json-ld")
    merged = Graph()
    for _name, graph in substrate_slices(document, merge_meta=False):
        for triple in graph:
            merged.add(triple)
    return merged


def substrate_slices(
    document: Mapping[str, Any],
    *,
    merge_meta: bool = True,
) -> list[tuple[str, Graph]]:
    """One graph per named graph in a substrate document.

    **Why not the union.** The substrate's unit is one platform's slice of one
    run, and an entity with two sources is asserted in two of them — the same
    IRI, a different `pulse:partOfRun` in each. That is the layer working as
    designed, but `RawPersonShape` caps `pulse:partOfRun` at `sh:maxCount 1`,
    so flattening the dataset first reports every multi-source entity as a
    violation of a constraint nothing violated. The same artefact appears
    without any splitting at all, as soon as two *runs* over one repository are
    validated together: this was latent, and per-source attribution is only
    what made it show up.

    A slice is validated with two things merged in, and both are needed for a
    different reason:

    - **The meta graph**, because `pulse:partOfRun` is constrained
      `sh:class pulse:ExtractionOutput` and the outputs live there. Without it
      every anchor in every slice is a dangling reference.
    - **Every `rdf:type` triple in the document**, because the other `sh:class`
      constraints point *sideways*: `pulse:owns` names a repository, `org:unitOf`
      names an organization, and the target frequently sits in another slice.
      Types alone, not the targets' properties — that is the least that lets a
      reference resolve, and it is what keeps cardinality per-slice. Merging the
      properties too would be the union again.

    So the target is "this source's assertions, with references resolved against
    what the whole run knows", which is what a slice actually claims. A node
    appearing only as an imported type has no properties, and every raw shape is
    minCount-free, so it conforms trivially rather than being half-checked.
    """
    dataset = Dataset()
    dataset.parse(data=json.dumps(dict(document)), format="json-ld")

    by_name: dict[str, Graph] = {}
    meta = Graph()
    types = Graph()
    for subject, predicate, obj, graph in dataset.quads((None, None, None, None)):
        name = str(getattr(graph, "identifier", graph))
        target = meta if name.endswith(_META_GRAPH_SUFFIX) else by_name.setdefault(
            name,
            Graph(),
        )
        target.add((subject, predicate, obj))
        if predicate == RDF.type:
            types.add((subject, predicate, obj))

    if not by_name:
        return [("", meta)] if len(meta) else []
    if not merge_meta:
        return [*by_name.items(), (_META_GRAPH_SUFFIX, meta)]

    slices: list[tuple[str, Graph]] = []
    for slice_name, slice_graph in by_name.items():
        combined = Graph()
        for source in (slice_graph, meta, types):
            for triple in source:
                combined.add(triple)
        slices.append((slice_name, combined))
    return slices


def validate_layer(
    document: Mapping[str, Any],
    layer: Layer,
    *,
    quads: bool = False,
) -> LayerValidationResult:
    """Validate one layer document. Never raises on a violation."""
    available, shapes = _shapes_for(layer)
    if not available or shapes is None:
        return LayerValidationResult(
            layer=layer,
            conforms=True,
            skipped=True,
            reason="ontology submodule not prepared",
        )
    try:
        if quads:
            return _validate_slices(document, layer, shapes)
        data = _graph_from_document(document, quads=False)
        result = SHACLValidator().validate_graph(data, shapes)
    except SHACLRuntimeUnavailableError as exc:
        return LayerValidationResult(
            layer=layer,
            conforms=True,
            skipped=True,
            reason=str(exc),
        )
    return LayerValidationResult(
        layer=layer,
        conforms=result.conforms,
        violations=list(result.violations),
        warnings=list(result.warnings),
        triples=len(data),
    )


def _validate_slices(
    document: Mapping[str, Any],
    layer: Layer,
    shapes: Graph,
) -> LayerValidationResult:
    """Validate each named graph against `shapes`, and aggregate.

    Conforming means *every* slice conforms; the violations are concatenated
    with the graph name attached, because "which source's slice is malformed"
    is the first thing anyone reading the report needs and the merged view
    cannot answer it.

    `triples` counts the union rather than the sum: the meta graph is merged
    into every slice for reference resolution, and summing would count it once
    per platform.
    """
    validator = SHACLValidator()
    conforms = True
    violations: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    for name, graph in substrate_slices(document):
        result = validator.validate_graph(graph, shapes)
        conforms = conforms and result.conforms
        violations.extend({**violation, "graph": name} for violation in result.violations)
        warnings.extend({**warning, "graph": name} for warning in result.warnings)

    return LayerValidationResult(
        layer=layer,
        conforms=conforms,
        violations=violations,
        warnings=warnings,
        triples=len(_graph_from_document(document, quads=True)),
    )


def validate_substrate(document: Mapping[str, Any]) -> LayerValidationResult:
    """Check a substrate slice against the raw shapes. Reports, never refuses.

    The substrate is append-only and durable: refusing to write a slice
    because one entity is malformed loses the other entities in it for good,
    and the raw shapes are open precisely so a source can assert something the
    canonical layer has no slot for.
    """
    return validate_layer(document, Layer.SUBSTRATE, quads=True)


def enforce_canonical(document: Mapping[str, Any]) -> LayerValidationResult:
    """Check the canonical graph and **raise** if it does not conform.

    The enforcing half of the split, and the change phase 6 is actually about:
    `pipeline/stages` still runs a warning-only `shacl_gate` over the
    in-request projection, deliberately, because there the alternative is
    returning nothing to a caller who asked for a graph. Here the alternative
    is leaving the previous canonical graph in place, which still answers
    queries — so refusing is strictly better than publishing.
    """
    result = validate_layer(document, Layer.CANONICAL, quads=True)
    if result.skipped:
        logger.warning("canonical gate %s", result.summary())
        return result
    if not result.conforms:
        raise CanonicalValidationError(result.violations)
    return result


__all__ = [
    "CanonicalValidationError",
    "Layer",
    "LayerValidationResult",
    "enforce_canonical",
    "substrate_slices",
    "validate_layer",
    "validate_substrate",
]
