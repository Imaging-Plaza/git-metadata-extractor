"""Fetch the tested sources revision, without changing existing checkouts.

Uses only the standard library so setup works before installing either project.
"""

# Standalone bootstrap CLI: readable validation errors, no installed package.
# ruff: noqa: INP001, TRY003, EM101, EM102

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = ROOT / "config/services/sources.lock.json"
CHECKOUT = ROOT / "dev/open-pulse-sources"


def load_lock(path: Path = LOCK_PATH) -> dict[str, str]:
    lock = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(lock, dict)
        or set(lock) != {"repository", "revision", "version"}
        or not all(isinstance(value, str) and value for value in lock.values())
        or not re.fullmatch(r"[0-9a-f]{40}", lock["revision"])
        or not lock["repository"].startswith("https://github.com/")
    ):
        raise ValueError(
            "Sources lock needs a GitHub repository, full commit SHA and version",
        )
    return lock


def git(path: Path, *args: str) -> str:
    result = subprocess.run(  # noqa: S603 - fixed executable, argv only, no shell
        ["git", "-C", str(path), *args],  # noqa: S607 - developer's Git on PATH
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    if result.returncode:
        # Do not echo git stderr: authenticated remote URLs may contain secrets.
        raise RuntimeError(
            f"Git {args[0]} failed; check repository access and the published revision",
        )
    return result.stdout.strip()


def normalized_remote(value: str) -> str:
    return (
        value.removesuffix(".git")
        .removesuffix("/")
        .replace(
            "git@github.com:",
            "https://github.com/",
        )
    )


def existing_revision(path: Path, repository: str) -> str | None:
    """Validate ownership/cleanliness; an empty interrupted checkout has no revision."""
    if not (path / ".git").exists():
        raise RuntimeError("Destination exists but is not an independent Git checkout; left unchanged")
    if Path(git(path, "rev-parse", "--show-toplevel")).resolve() != path.resolve():
        raise RuntimeError("Destination is not the expected Git checkout; left unchanged")
    if normalized_remote(git(path, "remote", "get-url", "origin")) != normalized_remote(repository):
        raise RuntimeError("Existing checkout has a different origin; left unchanged")
    if git(path, "status", "--porcelain"):
        raise RuntimeError("Existing checkout has uncommitted work; left unchanged")
    try:
        return git(path, "rev-parse", "--verify", "HEAD")
    except RuntimeError:
        if any(entry.name != ".git" for entry in path.iterdir()):
            raise RuntimeError("Incomplete sources checkout contains files; left unchanged") from None
        return None


def ensure_checkout(
    path: Path, repository: str, revision: str, *, check: bool = False,
) -> None:
    if path.exists():
        head = existing_revision(path, repository)
        if head is not None and head != revision:
            raise RuntimeError("Existing checkout has a different revision; review the lock/branch before updating it")
        if head is not None:
            return
        if check:
            raise RuntimeError("Incomplete sources checkout; run setup to retry its initial fetch")
    elif check:
        raise RuntimeError("Sources checkout is missing; run just sources-setup")
    else:
        path.mkdir(parents=True)
        git(path, "init", "--quiet")
        git(path, "remote", "add", "origin", repository)
    try:
        git(path, "fetch", "--depth=1", "origin", revision)
        git(path, "checkout", "--detach", "FETCH_HEAD")
    except RuntimeError as exc:
        raise RuntimeError(
            "Cannot fetch the pinned sources commit. Publish that revision first and verify access. "
            "The partial checkout was preserved; no branch fallback was used.",
        ) from exc
    if git(path, "rev-parse", "HEAD") != revision:
        raise RuntimeError("Fetched sources revision did not match the lock")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate the local checkout without network or writes",
    )
    args = parser.parse_args()
    try:
        lock = load_lock()
        ensure_checkout(
            CHECKOUT, lock["repository"], lock["revision"], check=args.check,
        )
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"Sources setup: {exc}", file=sys.stderr)
        return 1
    print(
        f"Sources checkout verified: {CHECKOUT} @ {lock['revision']} ({lock['version']})",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
