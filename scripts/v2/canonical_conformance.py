# ruff: noqa: INP001
"""Measure how close the canonical projection is to the v3 SHACL shapes.

The v3 flip cannot be judged by the corpus differ — that compares v2 output to
v2 output. What matters is whether `project_canonical` produces a graph the
*real* `ontology-shapes-canonical.ttl` accepts. This runs the projection over a
corpus directory, validates each result, and reports violations grouped by
shape and constraint so the remaining work is a list rather than a feeling.

    python scripts/v2/canonical_conformance.py data/corpus/after-provrun

`--layer raw` and `--layer substrate` do the same for the other two v3 layers.
The substrate one is the interesting case: it validates each named graph
**separately** (merged with the run's meta graph, so the anchors resolve) and
separately counts entities left in that meta graph. That count is the one
property of the substrate no validator can check: a node in the wrong named
graph conforms exactly as well as one in the right place, so a stranded
organization or membership passes SHACL while asserting that a fact about the
world is a fact about the extraction.

Needs the ontology prepared (`just ontology-prepare`).
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from prepare_ontology import ONTOLOGY_DIR  # noqa: E402

from git_metadata_extractor.pipeline.stages.canonical_projection import (  # noqa: E402
    project_canonical,
)

#: What legitimately belongs in a run's meta graph: the extraction's own
#: description. Anything else there is an entity that found no source.
#:
#: `pulse:SourceSnapshot` is what each output `prov:used` — the source as of
#: one index build. It describes the extraction as much as the run descriptor
#: does, which is why it is not a stranded entity.
_META_GRAPH_TYPES = frozenset(
    {
        "pulse:ExtractionOutput",
        "pulse:ExtractionRun",
        "prov:SoftwareAgent",
        "pulse:SourceSnapshot",
    },
)

#: The enumeration files must be loaded with the shapes: every enumerated
#: property is constrained by `sh:class`, which needs the instance triples to
#: resolve. Without them every discipline and platform value is a violation.
_ONTOLOGY_FILES = {
    "canonical": (
        "ontology-shapes-canonical.ttl",
        "ontology-definitions-canonical.ttl",
        "ontology-enumerations-canonical.ttl",
        "ontology-enumerations-raw.ttl",
    ),
    "raw": (
        "ontology-shapes-raw.ttl",
        "ontology-definitions-raw.ttl",
        "ontology-definitions-canonical.ttl",
        "ontology-enumerations-canonical.ttl",
        "ontology-enumerations-raw.ttl",
    ),
}
#: The substrate is the raw layer grouped into named graphs, so it validates
#: against the same shapes — what differs is the *document*, not the vocabulary.
_ONTOLOGY_FILES["substrate"] = _ONTOLOGY_FILES["raw"]


def _shapes_graph(layer: str):
    from rdflib import Graph  # noqa: PLC0415

    graph = Graph()
    for name in _ONTOLOGY_FILES[layer]:
        graph.parse(str(ONTOLOGY_DIR / name), format="turtle")
    return graph


def _context() -> dict:
    path = REPO_ROOT / "git_metadata_extractor" / "schema" / "generated" / "context.jsonld"
    return json.loads(path.read_text(encoding="utf-8"))["@context"]


def _validate(doc: dict, shapes, context: dict, *, quads: bool = False):
    """Validate exactly the way the live gate does.

    `ont_graph=` alone does not make the enumeration instance triples visible
    to `sh:class` — every `pulse:platform` and `pulse:repositoryType` value
    reads as a violation. `validation/shacl_validation.py` unions the data with
    the ontology graph before validating, which is why the live gate can check
    enumerations at all. Measuring any other way measures the wrong thing.

    `quads=True` for the substrate layer: it is a named-graph document, and
    each named graph is validated **separately**, merged only with the run's
    meta graph so that `pulse:partOfRun` can resolve to the
    `pulse:ExtractionOutput` it names. Validating the union instead reports a
    multi-source entity — the same IRI anchored to a different output in each
    slice — as exceeding `sh:maxCount 1` on `pulse:partOfRun`, a violation that
    exists only in the merge. The slicing itself is imported from
    `validation.layers` rather than repeated here: this function exists to
    measure what the live gate does, so it has to be the same code.
    """
    from rdflib import Graph  # noqa: PLC0415

    from git_metadata_extractor.validation import SHACLValidator  # noqa: PLC0415
    from git_metadata_extractor.validation.layers import (  # noqa: PLC0415
        substrate_slices,
    )

    payload = dict(doc)
    payload["@context"] = context
    validator = SHACLValidator()
    if not quads:
        data = Graph().parse(data=json.dumps(payload), format="json-ld")
        return validator.validate_graph(data, shapes), len(data)

    aggregate = _SliceResult()
    union = Graph()
    for _name, graph in substrate_slices(payload):
        aggregate.absorb(validator.validate_graph(graph, shapes))
        for triple in graph:
            union.add(triple)
    aggregate.triples = len(union)
    return aggregate, aggregate.triples


@dataclasses.dataclass
class _SliceResult:
    """The per-slice results, as one result the reporting code already reads.

    A run conforms when every one of its slices does, and the violations are
    concatenated. `triples` counts the union — the meta graph rides along with
    every slice, so summing the slices would count it once per platform.
    """

    conforms: bool = True
    violations: list = dataclasses.field(default_factory=list)
    triples: int = 0

    def absorb(self, result) -> None:
        self.conforms = self.conforms and result.conforms
        self.violations.extend(result.violations)


RUN_ID = "conformance"


def _run_descriptor(seed: str) -> dict:
    """A fixed `ExtractionRun`, so the projection is reproducible run to run."""
    from git_metadata_extractor.pipeline.stages.extraction_run import (  # noqa: PLC0415
        build_extraction_run,
    )

    return build_extraction_run(
        run_id=RUN_ID,
        seeds=[seed],
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ended_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        package_name="git-metadata-extractor",
        package_version=RUN_ID,
    )


def _project(layer: str, graph_nodes: list, context: dict, seed: str) -> dict:
    """One corpus result, projected into the layer under test."""
    if layer == "canonical":
        return project_canonical(graph_nodes)

    run_doc = _run_descriptor(seed)
    if layer == "substrate":
        from git_metadata_extractor.pipeline.stages.substrate import (  # noqa: PLC0415
            build_substrate,
        )

        return build_substrate(
            graph_nodes,
            run_id=RUN_ID,
            context=context,
            run_nodes=run_doc["@graph"],
        )

    from git_metadata_extractor.pipeline.stages.raw_projection import (  # noqa: PLC0415
        project_raw,
    )

    return project_raw(graph_nodes, run_id=RUN_ID, run_nodes=run_doc["@graph"])


def _stranded_types(document: dict) -> collections.Counter[str]:
    """Entities sitting in the meta graph — a routing defect SHACL cannot see.

    Every such case found so far validated cleanly while attributing a fact
    about an entity to the extraction itself. Counted here because nothing
    else will.
    """
    from git_metadata_extractor.pipeline.stages.substrate import (  # noqa: PLC0415
        meta_graph_iri,
    )

    meta = meta_graph_iri(RUN_ID)
    found: collections.Counter[str] = collections.Counter()
    for entry in document["@graph"]:
        if entry["@id"] != meta:
            continue
        for node in entry["@graph"]:
            node_type = str(node.get("@type") or "?")
            if node_type not in _META_GRAPH_TYPES:
                found[node_type] += 1
    return found


def _node_count(layer: str, document: dict) -> int:
    if layer != "substrate":
        return len(document["@graph"])
    return sum(len(entry["@graph"]) for entry in document["@graph"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--layer",
        choices=("canonical", "raw", "substrate"),
        default="canonical",
    )
    args = parser.parse_args()

    shapes = _shapes_graph(args.layer)
    context = _context()

    files = sorted(Path(args.directory).glob("*.json"))
    if args.limit:
        files = files[: args.limit]

    conforming = 0
    considered = 0
    by_constraint: collections.Counter[str] = collections.Counter()
    by_path: collections.Counter[str] = collections.Counter()
    stranded: collections.Counter[str] = collections.Counter()
    graph_counts: collections.Counter[int] = collections.Counter()
    nodes_in = nodes_out = 0

    for path in files:
        body = json.loads(path.read_text(encoding="utf-8"))
        graph_nodes = ((body.get("result") or {}).get("output") or {}).get("@graph")
        if not graph_nodes:
            continue
        considered += 1
        nodes_in += len(graph_nodes)
        projected = _project(
            args.layer,
            graph_nodes,
            context,
            body.get("source_url") or "urn:unknown",
        )
        nodes_out += _node_count(args.layer, projected)
        if args.layer == "substrate":
            stranded.update(_stranded_types(projected))
            graph_counts[len(projected["@graph"])] += 1

        result, _triples = _validate(
            projected,
            shapes,
            context,
            quads=args.layer == "substrate",
        )
        if result.conforms:
            conforming += 1
            continue
        for violation in result.violations:
            raw_path = str(violation.get("path") or "?")
            by_path[raw_path.rsplit("#", maxsplit=1)[-1].rsplit("/", 1)[-1]] += 1
            # Truncate hard: the message embeds the focus node, so grouping on
            # the full text would make every violation its own bucket.
            message = str(violation.get("message") or "?")
            by_constraint[message.split("<", maxsplit=1)[0].strip()[:60] or message[:60]] += 1

    print(f"layer                : {args.layer}")
    print(f"runs considered      : {considered}")
    print(f"conforming           : {conforming}  ({conforming / max(considered, 1):.0%})")
    print(f"nodes in -> out      : {nodes_in} -> {nodes_out}")
    if args.layer == "substrate":
        spread = ", ".join(f"{n}x{c}" for n, c in sorted(graph_counts.items()))
        print(f"named graphs per run : {spread}")
        print(
            "entities stranded    : "
            + (
                ", ".join(f"{t} x{c}" for t, c in stranded.most_common())
                if stranded
                else "none — every entity is in a source-attributed graph"
            ),
        )
    print()
    print("violations by message:")
    for name, count in by_constraint.most_common():
        print(f"  {count:6d}  {name}")
    print()
    print("violations by property:")
    for name, count in by_path.most_common(15):
        print(f"  {count:6d}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
