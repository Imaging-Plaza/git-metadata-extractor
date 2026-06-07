# ruff: noqa: INP001
"""Phase-1 parity harness: in-process gimie vs the gimie-api sidecar.

Runs both extractors on a set of repos and diffs the JSON-LD `@graph` (normalised
by `@id`). Same gimie version on both sides ⇒ expect zero semantic diff.

Prereqs:
  - the sidecar running and reachable, e.g.:
      docker compose -f .devcontainer/docker-compose.yml up -d gme-gimie-api
  - GIMIE_API_URL set, e.g. GIMIE_API_URL=http://localhost:7000
  - GME_GITHUB_TOKEN exported (the sidecar reads ACCESS_TOKEN; this process uses
    in-process gimie which reads the usual token env).

Usage:
  GIMIE_API_URL=http://localhost:7000 python scripts/v2/gimie_api_parity.py \
      sdsc-ordes/gimie CSBDeep/CSBDeep psf/requests
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

from src.v1.gimie_utils.gimie_methods import extract_gimie
from src.v2.ingest.providers.gimie_api_client import (
    extract_gimie_via_api,
    gimie_api_base,
)

_DEFAULT_REPOS = ["sdsc-ordes/gimie", "CSBDeep/CSBDeep", "psf/requests"]


def _nodes_by_id(payload: Any) -> dict[str, Any]:
    graph = payload.get("@graph") if isinstance(payload, dict) else payload
    if not isinstance(graph, list):
        return {}
    out: dict[str, Any] = {}
    for node in graph:
        if isinstance(node, dict):
            out[str(node.get("@id", f"_blank_{len(out)}"))] = node
    return out


def _diff(a: Any, b: Any) -> list[str]:
    ai, bi = _nodes_by_id(a), _nodes_by_id(b)
    msgs: list[str] = []
    only_a = sorted(set(ai) - set(bi))
    only_b = sorted(set(bi) - set(ai))
    if only_a:
        msgs.append(f"  only in-process: {only_a}")
    if only_b:
        msgs.append(f"  only api:        {only_b}")
    msgs.extend(
        f"  differs: {key}"
        for key in sorted(set(ai) & set(bi))
        if json.dumps(ai[key], sort_keys=True) != json.dumps(bi[key], sort_keys=True)
    )
    return msgs


def main(repos: list[str]) -> int:
    if gimie_api_base() is None:
        print("ERROR: set GIMIE_API_URL to the sidecar, e.g. http://localhost:7000")
        return 2
    full = 0
    for repo in repos:
        url = repo if repo.startswith("http") else f"https://github.com/{repo}"
        print(f"\n=== {url} ===")
        in_proc = extract_gimie(url)
        via_api = extract_gimie_via_api(url)
        if via_api is None:
            print("  API returned None (sidecar error/timeout) — investigate")
            continue
        diffs = _diff(in_proc, via_api)
        if diffs:
            print(f"  {len(diffs)} difference(s):")
            print("\n".join(diffs))
        else:
            full += 1
            print("  PARITY ✓ (identical @graph)")
    print(f"\n{full}/{len(repos)} fully identical")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:] or _DEFAULT_REPOS
    os.environ.setdefault("GIMIE_API_URL", os.getenv("GIMIE_API_URL", ""))
    raise SystemExit(main(args))
