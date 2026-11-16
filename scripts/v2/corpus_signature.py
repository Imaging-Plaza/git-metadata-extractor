# ruff: noqa: INP001
"""Semantic signatures for the behaviour-freeze corpus (Phase 0).

Why not diff the raw `/v2/extract` payloads? Because three kinds of churn make
a byte diff useless:

1. `identifiers.uuid` is a fresh UUIDv4 every run (`agents/models.py::generate_uuid`),
   and entities with no strong identifier are *keyed* on it (`urn:pulse:<uuid>`),
   so their `@id` changes run to run.
2. Timings and token counts land in `stats`.
3. Key order and list order are not stable across refactors — and the refactor
   deliberately reorders stages.

So we compare what the pipeline *means*: which entities exist, of what type,
with which scalar properties, and which edges connect them. UUID-keyed ids are
replaced by a hash of the entity's own stable content, which is reproducible
across runs as long as the content is.

    # record a baseline from a batch_extract output directory
    python scripts/v2/corpus_signature.py record data/corpus/baseline \
        -o tests/v2/corpus/baseline.signature.json

    # compare a later run against it
    python scripts/v2/corpus_signature.py diff \
        tests/v2/corpus/baseline.signature.json data/corpus/after
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)

# Dropped before hashing: per-run values, and internal `_*` metadata which is
# stripped from external artefacts anyway.
VOLATILE_KEYS = frozenset({"identifiers", "uuid", "stats", "parsedTimestamp"})


def _is_volatile(key: str) -> bool:
    return key in VOLATILE_KEYS or key.startswith("_")


def _scalars(node: dict[str, Any]) -> dict[str, Any]:
    """Scalar (non-reference) properties of a node, sorted, volatiles removed."""
    out: dict[str, Any] = {}
    for key, value in sorted(node.items()):
        if key in {"@id", "@type"} or _is_volatile(key):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value
        elif isinstance(value, list) and all(
            isinstance(v, (str, int, float, bool)) for v in value
        ):
            out[key] = sorted(str(v) for v in value)
    return out


def _refs(node: dict[str, Any]) -> list[tuple[str, str]]:
    """(predicate, target-id) pairs for every reference this node holds."""
    edges: list[tuple[str, str]] = []
    for key, value in node.items():
        if key in {"@id", "@type"} or _is_volatile(key):
            continue
        candidates = value if isinstance(value, list) else [value]
        edges.extend(
            (key, item["@id"])
            for item in candidates
            if isinstance(item, dict) and isinstance(item.get("@id"), str)
        )
    return edges


def _stable_id(node: dict[str, Any]) -> str:
    """A run-stable id. UUID-keyed ids are replaced by a content hash."""
    node_id = node.get("@id")
    if not isinstance(node_id, str) or not node_id:
        node_id = ""
    if node_id and not UUID_RE.search(node_id):
        return node_id
    payload = json.dumps(
        {"type": node.get("@type"), "props": _scalars(node)},
        sort_keys=True,
        default=str,
    )
    digest = hashlib.sha256(payload.encode()).hexdigest()[:10]
    return f"urn:pulse:content-{digest}"


#: Substitutions applied before bucketing a warning, so the *kind* is stable
#: across runs. Splitting on ":" alone was not enough: messages like
#: `Excluded organization entity '<uuid>' ...` put a fresh UUID in the key, so
#: every run invented new kinds and the census could not be compared.
_WARNING_NOISE = (
    (re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I), "<uuid>"),
    (re.compile(r"https?://\S+"), "<iri>"),
    (re.compile(r"\b\d+\b"), "<n>"),
)


def _warning_kind(warning: object) -> str:
    """A run-stable bucket key for one warning message."""
    text = str(warning)
    for pattern, placeholder in _WARNING_NOISE:
        text = pattern.sub(placeholder, text)
    # Keep the leading clause — it names the stage or check — then a short
    # prefix of the rest so distinct checks from one stage stay distinct.
    head, _, tail = text.partition(":")
    kind = head.strip() if not tail.strip() else f"{head.strip()}: {tail.strip()[:40]}"
    return kind[:80]


def signature_for(payload: dict[str, Any]) -> dict[str, Any]:
    """Reduce one /v2/extract payload to its semantic signature."""
    if payload.get("error_type"):
        return {"error": payload.get("error_type"), "detail": payload.get("detail")}

    # `POST /v2/extract` returns a job record that nests the whole response
    # under `result`; the sync `GET` route returns the response directly.
    # batch_extract.sh stores the job record, so both shapes reach this.
    body = payload
    if isinstance(payload.get("result"), dict):
        body = payload["result"]

    output = body.get("output")
    if not isinstance(output, dict):
        return {"error": "no-output"}

    nodes = output.get("@graph")
    if not isinstance(nodes, list):
        nodes = []
    nodes = [n for n in nodes if isinstance(n, dict)]

    id_map = {n.get("@id"): _stable_id(n) for n in nodes if isinstance(n.get("@id"), str)}

    entities = []
    edges = set()
    for node in nodes:
        sid = _stable_id(node)
        node_type = node.get("@type")
        entities.append(
            {
                "id": sid,
                "type": sorted(node_type) if isinstance(node_type, list) else node_type,
                "props": _scalars(node),
            },
        )
        for predicate, target in _refs(node):
            edges.add((sid, predicate, id_map.get(target, target)))

    # Warning *kinds*, not text: messages embed ids and counts that churn.
    kinds: dict[str, int] = {}
    for warning in body.get("warnings") or []:
        kinds[_warning_kind(warning)] = kinds.get(_warning_kind(warning), 0) + 1

    return {
        "entities": sorted(entities, key=lambda e: (str(e["id"]), str(e["type"]))),
        "edges": sorted(edges),
        "warning_kinds": dict(sorted(kinds.items())),
        "counts": {
            "entities": len(entities),
            "edges": len(edges),
        },
    }


#: Below this wall-clock/reported-duration ratio a result was replayed from the
#: pipeline cache rather than computed. Measured separation is stark — cached
#: runs sit at ~0.000, fresh ones at ~1.4 — so the threshold is not delicate.
_CACHE_RATIO_FLOOR = 0.25


def _cache_served_fraction(directory: Path) -> tuple[int, int]:
    """How many results in `directory` were replayed from the pipeline cache.

    This exists because a cache hit is nearly invisible in the result file. The
    pipeline cache stores the *whole* response, so `stats.duration_ms` comes
    back as the original run's duration and `stages_completed` lists every
    stage — a replayed result looks exactly like a fresh one. Only the job
    envelope's `started_at`/`completed_at` reflect the work this run actually
    did, and for a cache hit that is ~1ms against a reported 20+ seconds.

    Without this check it is entirely possible to "verify" a refactor against a
    corpus that was served from cache, and conclude the diff is empty when
    nothing was recomputed. That happened; hence the guard.
    """
    cached = 0
    considered = 0
    for path in sorted(directory.glob("*.json")):
        if path.name.startswith("_"):
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        started, completed = record.get("started_at"), record.get("completed_at")
        stats = (record.get("result") or {}).get("stats") or {}
        reported = stats.get("duration_ms")
        if not (started and completed) or not isinstance(reported, (int, float)):
            continue
        if reported <= 0:
            continue
        try:
            begin = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
            end = datetime.fromisoformat(str(completed).replace("Z", "+00:00"))
        except ValueError:
            continue
        considered += 1
        wall_ms = (end - begin).total_seconds() * 1000
        if wall_ms / float(reported) < _CACHE_RATIO_FLOOR:
            cached += 1
    return cached, considered


def _warn_if_cache_served(directory: Path) -> bool:
    cached, considered = _cache_served_fraction(directory)
    if considered and cached > considered // 2:
        print(
            f"\n!! {cached}/{considered} results in {directory} were replayed from "
            "the pipeline cache.\n"
            "   A diff against these compares the baseline with itself and proves "
            "nothing.\n"
            "   Re-run the server with V2_PIPELINE_CACHE_ENABLED=false and collect "
            "the corpus again.\n",
        )
        return True
    return False


def _load_run(directory: Path) -> dict[str, dict[str, Any]]:
    """Signature per result file in a batch_extract output directory."""
    signatures: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob("*.json")):
        if path.name.startswith("_"):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            signatures[path.stem] = {"error": f"unreadable: {exc}"}
            continue
        signatures[path.stem] = signature_for(payload)
    return signatures


def _cmd_record(args: argparse.Namespace) -> int:
    directory = Path(args.directory)
    if _warn_if_cache_served(directory) and not args.allow_cached:
        print("refusing to record a cache-served run; pass --allow-cached to override")
        return 2
    runs = _load_run(directory)
    if not runs:
        print(f"no result files in {args.directory}")
        return 1
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(runs, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    entities = sum(r.get("counts", {}).get("entities", 0) for r in runs.values())
    errors = sum(1 for r in runs.values() if r.get("error"))
    print(f"recorded {len(runs)} results -> {out}")
    print(f"  {entities} entities total, {errors} errored")
    return 0


def _cmd_diff(args: argparse.Namespace) -> int:
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    if Path(args.current).is_dir() and _warn_if_cache_served(Path(args.current)) and not args.allow_cached:
        print("refusing to diff a cache-served run; pass --allow-cached to override")
        return 2
    current = (
        _load_run(Path(args.current))
        if Path(args.current).is_dir()
        else json.loads(Path(args.current).read_text(encoding="utf-8"))
    )

    only_baseline = sorted(set(baseline) - set(current))
    only_current = sorted(set(current) - set(baseline))
    changed: list[str] = []

    for slug in sorted(set(baseline) & set(current)):
        before, after = baseline[slug], current[slug]
        be = {json.dumps(e, sort_keys=True) for e in before.get("entities", [])}
        ae = {json.dumps(e, sort_keys=True) for e in after.get("entities", [])}
        bg = {tuple(x) for x in before.get("edges", [])}
        ag = {tuple(x) for x in after.get("edges", [])}
        if be == ae and bg == ag and before.get("error") == after.get("error"):
            continue
        changed.append(slug)
        if args.verbose:
            print(f"\n=== {slug}")
            for item in sorted(be - ae)[:5]:
                print(f"  - entity {json.dumps(json.loads(item))[:150]}")
            for item in sorted(ae - be)[:5]:
                print(f"  + entity {json.dumps(json.loads(item))[:150]}")
            for edge in sorted(bg - ag)[:5]:
                print(f"  - edge {edge}")
            for edge in sorted(ag - bg)[:5]:
                print(f"  + edge {edge}")

    print()
    print(f"identical : {len(set(baseline) & set(current)) - len(changed)}")
    print(f"changed   : {len(changed)}")
    print(f"missing   : {len(only_baseline)}")
    print(f"new       : {len(only_current)}")
    if changed and not args.verbose:
        print(f"\nchanged slugs (first 20): {changed[:20]}")
        print("re-run with --verbose to see per-entity differences")
    return 1 if (changed or only_baseline or only_current) else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    record = sub.add_parser("record", help="write a signature file from a run directory")
    record.add_argument("directory")
    record.add_argument("-o", "--output", required=True)
    record.add_argument("--allow-cached", action="store_true")
    record.set_defaults(func=_cmd_record)

    diff = sub.add_parser("diff", help="compare a run (or signature) against a baseline")
    diff.add_argument("baseline")
    diff.add_argument("current")
    diff.add_argument("-v", "--verbose", action="store_true")
    diff.add_argument("--allow-cached", action="store_true")
    diff.set_defaults(func=_cmd_diff)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
