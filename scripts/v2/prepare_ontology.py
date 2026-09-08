# ruff: noqa: INP001, S603, S607
"""Bring `vendor/open-pulse-ontology` to the state the generator expects.

The submodule stays pinned to an immutable upstream commit; everything this
repo needs beyond that lives as a patch in `ontology/patches/`. This script
puts the two together, idempotently, so the generator and CI can call it
without caring whether patches are already applied.

Idempotence matters more than it sounds: a half-applied series is worse than
none, because the generator would emit models for a shape set nobody can
reproduce. So the working tree is reset to the pin first, every time, and the
patches are re-applied from scratch.

    python scripts/v2/prepare_ontology.py            # prepare
    python scripts/v2/prepare_ontology.py --check    # verify only, exit 1 if not ready
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SUBMODULE = REPO_ROOT / "vendor" / "open-pulse-ontology"
PATCH_DIR = REPO_ROOT / "ontology" / "patches"
ONTOLOGY_DIR = SUBMODULE / "src" / "ontology"

#: Read by the generator. Kept here so there is one list of the files that
#: matter, rather than a glob that silently picks up new ones.
SHAPE_FILES = {
    "raw": "ontology-shapes-raw.ttl",
    "canonical": "ontology-shapes-canonical.ttl",
    "provenance": "ontology-shapes-provenance.ttl",
}
#: `git ls-tree` prints `<mode> <type> <sha>\t<path>`.
_LS_TREE_SHA_FIELD = 2

#: Read as a single merged graph by the generator: enumeration classes are
#: extended across layers (`pulse:PublicationTypeEnumeration` gains eight
#: members in the raw file), so neither file is authoritative alone.
ENUMERATION_FILES = (
    "ontology-enumerations-canonical.ttl",
    "ontology-enumerations-raw.ttl",
)

DEFINITION_FILES = (
    "ontology-definitions-canonical.ttl",
    "ontology-definitions-raw.ttl",
    "ontology-definitions-provenance.ttl",
    "ontology-enumerations-canonical.ttl",
    "ontology-enumerations-raw.ttl",
)


def _git(*args: str, cwd: Path = SUBMODULE) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def pinned_sha() -> str:
    """The SHA the superproject records for the submodule.

    Read from the **index** first, then the committed tree. Order matters: a
    freshly added or re-pointed submodule lives in the index until it is
    committed, and reading only `ls-tree HEAD` returns nothing in that window —
    so the HEAD-vs-pin comparison below silently passes.

    That is not hypothetical. `git submodule add` records the *default branch*
    tip, and a subsequent `git checkout <sha>` inside the submodule moves the
    worktree without updating the gitlink; staging it needs an explicit
    `git add vendor/open-pulse-ontology`. This function missed exactly that
    mismatch once, so it now looks where the pin actually is.
    """
    staged = _git("ls-files", "--stage", "vendor/open-pulse-ontology", cwd=REPO_ROOT)
    if staged.returncode == 0 and staged.stdout.strip():
        # `<mode> <sha> <stage>\t<path>`
        parts = staged.stdout.split()
        if len(parts) > 1:
            return parts[1]

    result = _git("ls-tree", "HEAD", "vendor/open-pulse-ontology", cwd=REPO_ROOT)
    if result.returncode != 0 or not result.stdout.strip():
        return ""
    parts = result.stdout.split()
    return parts[_LS_TREE_SHA_FIELD] if len(parts) > _LS_TREE_SHA_FIELD else ""


def patches() -> list[Path]:
    return sorted(PATCH_DIR.glob("[0-9][0-9]-*.patch"))


def prepare(*, check_only: bool) -> int:
    head = _git("rev-parse", "HEAD").stdout.strip()[:12]
    pin = pinned_sha()[:12]

    if not ONTOLOGY_DIR.is_dir():
        # Distinguish the two causes. Blaming "not checked out" when the
        # submodule is simply at the wrong commit sends people to the wrong
        # fix — `src/ontology/` only exists from the PR #25 restructure
        # onwards, so an older commit looks identical to a missing checkout.
        if not SUBMODULE.is_dir() or not head:
            _fail(
                f"{SUBMODULE.relative_to(REPO_ROOT)} is not checked out. "
                "Run: git submodule update --init --recursive",
            )
        detail = (
            f"submodule is at {head}"
            + (f" but the superproject pins {pin}" if pin and pin != head else "")
        )
        _fail(
            f"{ONTOLOGY_DIR.relative_to(REPO_ROOT)} does not exist — {detail}. "
            "That directory only exists from the PR #25 restructure onwards. "
            "Run: git submodule update --init --recursive --force",
        )

    if pin and head != pin:
        print(f"note: submodule HEAD is {head}, superproject pins {pin}")

    series = patches()

    if check_only:
        # "Ready" means every patch is already applied. `git apply --check -R`
        # succeeds only if the patch could be *reversed*, i.e. it is present.
        missing = [
            patch.name
            for patch in series
            if _git("apply", "--check", "-R", str(patch)).returncode != 0
        ]
        if missing:
            print("ontology not prepared; patches not applied: " + ", ".join(missing))
            return 1
        print(f"ontology ready: {head} + {len(series)} patch(es)")
        return 0

    # Always start from the pin — a partially applied series must not survive.
    reset = _git("checkout", "--", ".")
    if reset.returncode != 0:
        _fail(f"could not reset submodule worktree: {reset.stderr.strip()}")

    for patch in series:
        applied = _git("apply", str(patch))
        if applied.returncode != 0:
            _fail(
                f"{patch.name} does not apply to {head}. The pin probably moved; "
                f"rebase or drop the patch.\n{applied.stderr.strip()}",
            )
        print(f"  applied {patch.name}")

    if not series:
        print("  no patches — the pin alone describes the ontology")

    print(f"ontology ready: {head} + {len(series)} patch(es)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the ontology is prepared; do not modify anything",
    )
    args = parser.parse_args()
    return prepare(check_only=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
