"""Validation harness for the owner→ROR resolver against a gold set.

Replays the github-org → ROR resolution through the *current* cascade
(`_select_ror_parent`: web-domain A1 → distinctive-token nexus guard) and
compares the outcome to a gold file — the `gme7_ror_resolutions.txt` table
produced from a real run:

    github_owner            -> resolved_ROR                 ror_id   repos  ov=N  [<== SUSPECT ...]

Each row is labelled from the `ov=`/SUSPECT marker:
  * SUSPECT (ov=0, no distinctive overlap) → the *old* resolver likely matched
    a token-coincidental org; the new cascade should REJECT it or re-resolve it
    to a different (domain-confirmed) ROR.
  * non-suspect (ov>=1)                    → likely correct; the new cascade
    should KEEP it (not regress to standalone).

NOTE the gold labels are heuristic (the SUSPECT flag conflates "coincidental
wrong" with "concatenated-correct" like broadinstitute→Broad Institute), so the
harness reports both an aggregate and a per-row old→new table for human review
rather than treating ov=0 as ground truth. Run it in shadow mode — it never
writes; it only resolves and reports.

Usage
-----
    python scripts/v2/validate_ror_resolution.py gme7_ror_resolutions.txt
    python scripts/v2/validate_ror_resolution.py gme7_ror_resolutions.txt --limit 30
    python scripts/v2/validate_ror_resolution.py --self-test   # tiny embedded sample

Needs network (GitHub org homepage + live ROR) and a GitHub token in the env
(GITHUB_TOKEN / GME_GITHUB_TOKEN) for the homepage lookup; without one the
domain tier is skipped and only the nexus guard runs.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_ROW_RE = re.compile(
    r"^(?P<handle>[\w.\-]+)\s+->\s+(?P<name>.+?)\s+(?P<ror>[0-9a-z]{9})\s+\d+\s+ov=(?P<ov>\d+)",
)


@dataclass
class GoldRow:
    handle: str
    old_ror: str
    old_name: str
    ov: int

    @property
    def suspect(self) -> bool:
        return self.ov == 0


def parse_gold(path: Path) -> list[GoldRow]:
    rows: list[GoldRow] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _ROW_RE.match(line.strip())
        if m:
            rows.append(
                GoldRow(
                    handle=m["handle"],
                    old_ror=m["ror"],
                    old_name=m["name"].strip(),
                    ov=int(m["ov"]),
                ),
            )
    return rows


def _github_org_meta(handle: str, token: str | None) -> tuple[str | None, str | None]:
    """Return (homepage, display_name) for a github org/user, or (None, None).

    The display name is passed as ``org_name`` so the shadow run has the same
    name signal production does — otherwise the handle-only shortlist
    under-resolves correct matches and the preserved-rate is misleading.
    """
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    for kind in ("orgs", "users"):
        try:
            r = requests.get(
                f"https://api.github.com/{kind}/{handle}", headers=headers, timeout=10,
            )
        except Exception:  # noqa: BLE001
            continue
        if r.status_code == 200:
            data = r.json()
            return (data.get("blog") or data.get("html_url"), data.get("name"))
    return (None, None)


async def _resolve(
    handle: str, homepage: str | None, org_name: str | None, provider: Any,
) -> dict[str, Any] | None:
    from git_metadata_extractor.pipeline.stages.ownership_check import (  # noqa: PLC0415
        _github_handle_query_terms,
        _ror_candidate_shortlist,
        _select_ror_parent,
    )

    queries = _github_handle_query_terms(handle, org_name)
    shortlist = _ror_candidate_shortlist(
        handle=handle, org_name=org_name, ror_provider=provider,
        queries=queries, max_candidates=10, warnings=[],
    )
    if not shortlist:
        return None
    return await _select_ror_parent(
        handle=handle, org_name=org_name, shortlist=shortlist,
        parent_selector=None,
        org_context={"homepage": homepage} if homepage else None,
        warnings=[],
    )


async def run(rows: list[GoldRow], token: str | None) -> None:
    from git_metadata_extractor.providers.ror_provider import RealRORProvider  # noqa: PLC0415

    provider = RealRORProvider()
    suspect_total = suspect_resolved_away = 0
    correct_total = correct_preserved = 0
    print(f"{'handle':28s} {'OLD ROR':28s} {'NEW (cascade)':28s} verdict")
    print("-" * 100)
    for row in rows:
        homepage, org_name = _github_org_meta(row.handle, token)
        pick = await _resolve(row.handle, homepage, org_name, provider)
        new_name = (pick or {}).get("name")
        new_ror = (pick or {}).get("id", "") or ""
        new_ror = new_ror.rsplit("/", 1)[-1] if new_ror else None
        changed = new_ror != row.old_ror

        if row.suspect:
            suspect_total += 1
            # "resolved away" = the new cascade no longer keeps the suspect ROR
            if new_ror != row.old_ror:
                suspect_resolved_away += 1
            verdict = "SUSPECT→fixed" if changed else "SUSPECT→still"
        else:
            correct_total += 1
            if new_ror == row.old_ror:
                correct_preserved += 1
            verdict = "kept" if not changed else "CHANGED(review)"

        print(
            f"{row.handle:28.28s} {row.old_name:28.28s} "
            f"{str(new_name or '— standalone'):28.28s} {verdict}",
        )

    print("-" * 100)
    if suspect_total:
        print(
            f"SUSPECT pairs resolved away (rejected or re-resolved): "
            f"{suspect_resolved_away}/{suspect_total} "
            f"({100 * suspect_resolved_away / suspect_total:.0f}%)",
        )
    if correct_total:
        print(
            f"Non-suspect pairs preserved: "
            f"{correct_preserved}/{correct_total} "
            f"({100 * correct_preserved / correct_total:.0f}%)",
        )


_SELF_TEST = """
Edinburgh-Genome-Foundry -> Jøtul (Norway)                  042epp307     18  ov=0  <== SUSPECT
GoogleCloudPlatform      -> Google DeepMind (United Kingdom) 00971b260      2  ov=0  <== SUSPECT
broadinstitute           -> Broad Institute                 05a0ya142      6  ov=0  <== SUSPECT
google-deepmind          -> Google DeepMind (United Kingdom) 00971b260     89  ov=2
nanoporetech             -> Oxford Nanopore Technologies     04hyfx005      1  ov=0  <== SUSPECT
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("gold", nargs="?", help="Path to gme7_ror_resolutions.txt")
    ap.add_argument("--limit", type=int, default=None, help="Only the first N rows.")
    ap.add_argument("--self-test", action="store_true", help="Run the embedded sample.")
    args = ap.parse_args()

    if args.self_test:
        import tempfile  # noqa: PLC0415

        tmp = Path(tempfile.mktemp(suffix=".txt"))
        tmp.write_text(_SELF_TEST, encoding="utf-8")
        rows = parse_gold(tmp)
    elif args.gold:
        rows = parse_gold(Path(args.gold))
    else:
        ap.error("provide a gold file or --self-test")
        return 2

    if args.limit:
        rows = rows[: args.limit]
    print(f"Parsed {len(rows)} gold rows.\n")
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GME_GITHUB_TOKEN")
    if not token:
        print("(no GITHUB_TOKEN — domain tier skipped, nexus guard only)\n")
    asyncio.run(run(rows, token))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
