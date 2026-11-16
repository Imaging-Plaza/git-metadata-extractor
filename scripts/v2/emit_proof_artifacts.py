# ruff: noqa: INP001
"""Emit inspectable Turtle/TriG for the v3 layers, from real corpus output.

Reads two corpus directories — one recorded before the v3 flip (flat v2 shapes)
and one after (canonical v3) — and writes, for one repository:

    dev/v3-proof/canonical.ttl     the graph /v2/extract returns today
    dev/v3-proof/substrate.trig    the raw layer, in named graphs
    dev/v3-proof/run.ttl           the ExtractionRun + SoftwareAgent

The TriG is exactly what the substrate writer loads into the store: one named
graph per `pulse:ExtractionOutput`, i.e. per platform slice of a run. The
routing is **not** reimplemented here — this calls
`pipeline/stages/substrate.py`, the same code the `substrate_projection` stage
uses, so what you read is what gets written. (It did have its own copy of the
grouping while the writer did not exist; keeping that copy would mean the proof
could drift from the product.)

    python scripts/v2/emit_proof_artifacts.py ANTsX_ANTs
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rdflib import ConjunctiveGraph, Graph, URIRef  # noqa: E402

from git_metadata_extractor.pipeline.stages.extraction_run import (  # noqa: E402
    build_extraction_run,
)
from git_metadata_extractor.pipeline.stages.substrate import (  # noqa: E402
    build_substrate,
)

OUT_DIR = REPO_ROOT / "dev" / "v3-proof"
CONTEXT = (
    REPO_ROOT / "git_metadata_extractor" / "schema" / "generated" / "context.jsonld"
)
RUN_ID = "proof-0001"

PREFIXES = (
    ("pulse", "https://open-pulse.epfl.ch/ontology#"),
    ("schema", "http://schema.org/"),
    ("org", "http://www.w3.org/ns/org#"),
    ("prov", "http://www.w3.org/ns/prov#"),
    ("wd", "http://www.wikidata.org/entity/"),
)


def _graph_from(doc: dict, context: dict) -> Graph:
    payload = dict(doc)
    payload["@context"] = context
    return Graph().parse(data=json.dumps(payload), format="json-ld")


def _bind(graph: Graph | ConjunctiveGraph) -> None:
    for prefix, iri in PREFIXES:
        graph.bind(prefix, iri, override=True)


def _write(name: str, text: str) -> None:
    (OUT_DIR / name).write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slug", help="corpus result basename, e.g. ANTsX_ANTs")
    parser.add_argument("--flat-dir", default="data/corpus/after-canon")
    parser.add_argument("--canonical-dir", default="data/corpus/v3-final")
    args = parser.parse_args()

    context = json.loads(CONTEXT.read_text(encoding="utf-8"))["@context"]
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    def _load(directory: str) -> dict:
        path = REPO_ROOT / directory / f"{args.slug}.json"
        return json.loads(path.read_text(encoding="utf-8"))["result"]

    flat = _load(args.flat_dir)
    canonical = _load(args.canonical_dir)

    # --- canonical: what /v2/extract returns today -----------------------
    canon_nodes = canonical["output"]["@graph"]
    canon_graph = _graph_from({"@graph": canon_nodes}, context)
    _bind(canon_graph)
    _write("canonical.ttl", canon_graph.serialize(format="turtle"))

    # --- the run descriptor ----------------------------------------------
    run_doc = build_extraction_run(
        run_id=RUN_ID,
        seeds=[flat["source_url"]],
        started_at=datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc),
        ended_at=datetime(2026, 9, 8, 12, 0, 22, tzinfo=timezone.utc),
        package_name="git-metadata-extractor",
        package_version="3.0.0",
    )
    run_graph = _graph_from(run_doc, context)
    _bind(run_graph)
    _write("run.ttl", run_graph.serialize(format="turtle"))

    # --- substrate: raw layer, one named graph per ExtractionOutput ------
    substrate = build_substrate(
        flat["output"]["@graph"],
        run_id=RUN_ID,
        context=context,
        run_nodes=run_doc["@graph"],
        generated_at="2026-09-08T12:00:22+00:00",
    )
    by_graph = {
        entry["@id"]: entry["@graph"] for entry in substrate["@graph"]
    }

    # Serialised as TriG rather than the N-Quads the writer sends, because this
    # file is for reading: prefixes and nesting are the whole point of TriG,
    # and the quads it holds are identical either way.
    quads = ConjunctiveGraph()
    _bind(quads)
    for graph_name, nodes in by_graph.items():
        named = quads.get_context(URIRef(graph_name))
        for triple in _graph_from({"@graph": nodes}, context):
            named.add(triple)
    _write("substrate.trig", quads.serialize(format="trig"))

    print(f"wrote {OUT_DIR.relative_to(REPO_ROOT)}/")
    print(f"  canonical.ttl   {len(canon_graph):4d} triples, {len(canon_nodes)} nodes")
    print(f"  substrate.trig  {len(quads):4d} triples in named graphs:")
    for name, nodes in by_graph.items():
        print(f"      <{name}>  {len(nodes)} nodes")
    print(f"  run.ttl         {len(run_graph):4d} triples")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
