# ruff: noqa: INP001
"""Measure how close the canonical projection is to the v3 SHACL shapes.

The v3 flip cannot be judged by the corpus differ — that compares v2 output to
v2 output. What matters is whether `project_canonical` produces a graph the
*real* `ontology-shapes-canonical.ttl` accepts. This runs the projection over a
corpus directory, validates each result, and reports violations grouped by
shape and constraint so the remaining work is a list rather than a feeling.

    python scripts/v2/canonical_conformance.py data/corpus/after-provrun

Needs the ontology prepared (`just ontology-prepare`).
"""

from __future__ import annotations

import argparse
import collections
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


def _shapes_graph(layer: str):
    from rdflib import Graph  # noqa: PLC0415

    graph = Graph()
    for name in _ONTOLOGY_FILES[layer]:
        graph.parse(str(ONTOLOGY_DIR / name), format="turtle")
    return graph


def _context() -> dict:
    path = REPO_ROOT / "git_metadata_extractor" / "schema" / "generated" / "context.jsonld"
    return json.loads(path.read_text(encoding="utf-8"))["@context"]


def _validate(doc: dict, shapes, context: dict):
    """Validate exactly the way the live gate does.

    `ont_graph=` alone does not make the enumeration instance triples visible
    to `sh:class` — every `pulse:platform` and `pulse:repositoryType` value
    reads as a violation. `validation/shacl_validation.py` unions the data with
    the ontology graph before validating, which is why the live gate can check
    enumerations at all. Measuring any other way measures the wrong thing.
    """
    from rdflib import Graph  # noqa: PLC0415

    from git_metadata_extractor.validation import SHACLValidator  # noqa: PLC0415

    payload = dict(doc)
    payload["@context"] = context
    data = Graph().parse(data=json.dumps(payload), format="json-ld")
    result = SHACLValidator().validate_graph(data, shapes)
    return result, len(data)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--layer", choices=("canonical", "raw"), default="canonical")
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
    nodes_in = nodes_out = 0

    for path in files:
        body = json.loads(path.read_text(encoding="utf-8"))
        graph_nodes = ((body.get("result") or {}).get("output") or {}).get("@graph")
        if not graph_nodes:
            continue
        considered += 1
        nodes_in += len(graph_nodes)
        if args.layer == "raw":
            from git_metadata_extractor.pipeline.stages.extraction_run import (  # noqa: PLC0415
                build_extraction_run,
            )
            from git_metadata_extractor.pipeline.stages.raw_projection import (  # noqa: PLC0415
                project_raw,
            )

            run_doc = build_extraction_run(
                run_id="conformance",
                seeds=[body.get("source_url") or "urn:unknown"],
                started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                ended_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                package_name="git-metadata-extractor",
                package_version="conformance",
            )
            projected = project_raw(
                graph_nodes,
                run_id="conformance",
                run_nodes=run_doc["@graph"],
            )
        else:
            projected = project_canonical(graph_nodes)
        nodes_out += len(projected["@graph"])

        result, _triples = _validate(projected, shapes, context)
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
