# ruff: noqa: INP001
"""Run the store-side unifier over an Oxigraph store.

`PROVENANCE_ARCHITECTURE.md` phase 4. Reads every substrate slice the store has
accumulated, clusters records that describe the same entity, merges them, and
replaces `graph:canonical`.

    # report what unification would do, touching nothing
    python scripts/v2/unify.py http://localhost:7878 --dry-run

    # do it
    python scripts/v2/unify.py http://localhost:7878

A script rather than an endpoint on purpose: the architecture doc puts the
query and provenance API in phase 7, and committing to an HTTP surface now
would mean versioning a contract before the thing behind it has been run in
anger. `unify.runner.unify_store` is the entry point either way, so promoting
this to a route later is a handler, not a rewrite.

`--dry-run` is the mode to use first on a store you care about: unification
`DROP`s and rewrites `graph:canonical`, so seeing the report before the write
is the difference between a decision and a discovery. It skips the provenance
write too — `graph:prov` annotates the canonical triples, so recording
decisions about a graph that was not written would leave the two inconsistent.

Two graphs come out of a full pass:

    urn:pulse:graph:canonical   the entities, closed-shape valid, queryable
    urn:pulse:graph:prov        why each *chosen* value won, as RDF-star

`graph:prov` is upsert, not replace: `pulse:observationCount` and
`pulse:firstObservedOn` are history that survives across passes, unlike
`graph:canonical`, which is a pure function of the substrate and is rewritten
whole.

The canonical write is **gated** (phase 6): the graph is validated against the
closed canonical shapes *before* anything is dropped, and a violation aborts
with exit 3 leaving the previous graph intact. That gate is also the
sufficiency check — each layer passing its own shapes does not mean the
substrate holds what canonical needs. `--no-enforce` publishes anyway, for
inspecting a broken graph.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from git_metadata_extractor.store import OxigraphStore  # noqa: E402
from git_metadata_extractor.unify import unify_store  # noqa: E402
from git_metadata_extractor.validation import CanonicalValidationError  # noqa: E402

CONTEXT = (
    REPO_ROOT / "git_metadata_extractor" / "schema" / "generated" / "context.jsonld"
)

MAX_LISTED = 10


async def _run(args: argparse.Namespace) -> int:
    store = OxigraphStore(args.store_url, timeout=args.timeout)
    if not await store.is_available():
        print(f"error: no store answering at {args.store_url}", file=sys.stderr)
        return 1

    context = json.loads(CONTEXT.read_text(encoding="utf-8"))["@context"]
    try:
        report = await unify_store(
            store,
            context=context,
            write=not args.dry_run,
            provenance=not args.no_provenance,
            enforce=not args.no_enforce,
        )
    except CanonicalValidationError as exc:
        # The gate refused to publish, so `graph:canonical` still holds the
        # previous valid graph. That is the point of validating before the
        # DROP rather than after it.
        print(f"error: {exc}", file=sys.stderr)
        print(
            "graph:canonical was NOT replaced; the previous graph is intact.",
            file=sys.stderr,
        )
        for violation in exc.violations[:MAX_LISTED]:
            print(f"   {violation.get('focusNode')} -> {violation.get('message')}", file=sys.stderr)
        return 3

    return _report(report, args)


def _report(report: Any, args: argparse.Namespace) -> int:
    print(f"store            : {args.store_url}")
    print(f"mode             : {'dry-run (nothing written)' if args.dry_run else 'write'}")
    print(f"records read     : {report.records_read} from {report.graphs_read} graphs")
    print(f"clusters         : {report.clusters}")
    print(f"cross-run merges : {report.cross_graph_clusters}")
    print(f"entities         : {report.entities_written}")
    print(f"renamed (sameAs) : {report.aliases_written}")
    print()
    print("clusters by type:")
    for entity_type, count in sorted(report.by_type.items()):
        print(f"   {entity_type:34s} {count}")

    if report.provenance is not None:
        print()
        print(f"provenance       : {report.provenance.summary()}")
        print("   `new` are values not seen before; `reconfirmed` bump the")
        print("   observation counter and keep their firstObservedOn.")

    print()
    print(f"contested values : {len(report.contested)}")
    for selection in report.contested[:MAX_LISTED]:
        print(f"   {selection.subject}")
        print(
            f"       {selection.prop} = {selection.winner!r} "
            f"(over {', '.join(repr(loser) for loser in selection.losers)}) "
            f"[{selection.rule}]",
        )
    if len(report.contested) > MAX_LISTED:
        print(f"   ... and {len(report.contested) - MAX_LISTED} more")

    # An invariant, not a diagnostic: after the remap pass nothing may still
    # point at an IRI the unifier renamed away, and no recorded selection may
    # name a value the canonical graph does not hold.
    if report.stale:
        print()
        print(f"STALE REFERENCES : {len(report.stale)} entities — the remap has a hole")
        for iri, refs in list(report.stale.items())[:MAX_LISTED]:
            print(f"   {iri} -> {refs}")
        return 2

    # A diagnostic. Legitimate answers include SPDX licences and Wikidata
    # disciplines, so the useful signal is the *shape* of the list rather than
    # its length.
    if report.dangling:
        print()
        print(f"unresolved refs  : {len(report.dangling)} entities (diagnostic)")
        for iri, refs in list(report.dangling.items())[:5]:
            print(f"   {iri} -> {refs[:3]}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("store_url", help="Oxigraph server root, e.g. http://localhost:7878")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be written without touching graph:canonical",
    )
    parser.add_argument(
        "--no-provenance",
        action="store_true",
        help=(
            "skip the graph:prov write. The provenance graph annotates the "
            "canonical triples, so the two go together — this exists to "
            "isolate a unification problem from a provenance one, not as a "
            "normal mode."
        ),
    )
    parser.add_argument(
        "--no-enforce",
        action="store_true",
        help=(
            "publish graph:canonical even if it fails the closed-shape gate. "
            "For inspecting a broken graph, not for routine use — the default "
            "leaves the previous valid graph in place instead."
        ),
    )
    parser.add_argument("--timeout", type=float, default=60.0)
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
