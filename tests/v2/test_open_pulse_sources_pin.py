"""Cross-repo version pin coherence (GME task brief 02).

The RAG index layer lives in `sdsc-ordes/open-pulse-sources` since 3.0.0, and
this repo consumes it twice: as a **library** (the read-side providers,
`canonicalization/doi.py` and the gunicorn index bootstrap import it) and as a
**service image** (`gme-sources`, which owns the write side). Both must resolve
to the same child release, and neither may ride a mutable ref — a `main` /
`latest` default silently changes what a "reproducible" deployment runs.

The library pin in `pyproject.toml` is the single source of truth. These tests
fail when another install path re-pins the library behind its back, when the
`gme-sources` image tag drifts away from it, or when either default slips back
to a moving target.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from git_metadata_extractor.agents.rule_based._repo_signals import (
    _resolve_compose_image,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"
DEPLOY_COMPOSE = REPO_ROOT / "tools" / "deploy" / "docker-compose.yml"

PACKAGE = "open-pulse-sources"
CHILD_REPO_URL = "https://github.com/sdsc-ordes/open-pulse-sources"

# `open-pulse-sources @ git+<url>@<ref>` — the ref is what we care about.
_REQUIREMENT = re.compile(
    rf"{re.escape(PACKAGE)}\s*@\s*git\+{re.escape(CHILD_REPO_URL)}@(?P<ref>[^\"'\s]+)",
)
_SEMVER_TAG = re.compile(r"^v\d+\.\d+\.\d+(?:[-.].+)?$")
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")

# Install paths that must NOT carry their own copy of the pin. A `${VAR}`
# reference is fine (that is the documented Docker build-arg escape hatch);
# a literal ref is a second source of truth waiting to go stale.
_INSTALL_PATHS = (
    "justfile",
    "tools/image/Dockerfile",
    ".github/workflows/ci.yml",
    ".github/workflows/publish_image_in_GHCR.yaml",
    ".devcontainer/devcontainer.json",
    ".devcontainer/docker-compose.yml",
)

_MUTABLE_REFS = frozenset({"main", "master", "develop", "HEAD", "latest"})


def _library_pin() -> str:
    """The declared child library ref, from the single source of truth."""
    match = _REQUIREMENT.search(PYPROJECT.read_text(encoding="utf-8"))
    assert match is not None, (
        f"{PACKAGE} is not declared in pyproject.toml `dependencies`. It is a "
        "hard dependency — the read-side providers and the index bootstrap "
        "import it unconditionally, so a clean install without it is broken "
        "(ModuleNotFoundError: open_pulse_sources at import time)."
    )
    return match.group("ref")


def _sources_image() -> str:
    """The resolved default image of the gme-sources service."""
    compose = yaml.safe_load(DEPLOY_COMPOSE.read_text(encoding="utf-8"))
    service = compose["services"]["gme-sources"]
    image = _resolve_compose_image(service.get("image"))
    assert image is not None, (
        "gme-sources declares no resolvable default image — an unset "
        "SOURCES_IMAGE must still deploy a known revision"
    )
    return image


def test_library_pin_is_declared_and_immutable() -> None:
    ref = _library_pin()
    assert ref not in _MUTABLE_REFS, (
        f"{PACKAGE} is pinned to the mutable ref {ref!r}: the same parent "
        "revision would install different library code over time. Pin a "
        "release tag (vX.Y.Z) or a full commit SHA."
    )
    assert _SEMVER_TAG.match(ref) or _FULL_SHA.match(ref), (
        f"unrecognised {PACKAGE} ref {ref!r} — expected a release tag "
        "(vX.Y.Z) or a 40-character commit SHA"
    )


def test_sources_image_default_is_immutable() -> None:
    image = _sources_image()
    assert "@sha256:" in image or ":" in image.rsplit("/", 1)[-1], (
        f"gme-sources image {image!r} carries no tag or digest — it would "
        "resolve to :latest"
    )
    tag = image.rsplit("@", 1)[0].rsplit(":", 1)[-1] if "@" not in image else "digest"
    assert tag not in _MUTABLE_REFS, (
        f"gme-sources is pinned to the mutable tag {tag!r}; production "
        "defaults must name an immutable revision"
    )


def test_sources_image_matches_the_library_pin() -> None:
    """The imported library and the write-side service must be one release.

    They share DuckDB stores and Qdrant collections on `gme-data`; a schema or
    collection-layout change on either side of a version skew surfaces as
    empty search results or a bootstrap failure, not a loud error.
    """
    ref, image = _library_pin(), _sources_image()
    if "@sha256:" in image:
        pytest.skip("gme-sources pinned by digest — verified at deploy time")

    tag = image.rsplit(":", 1)[-1]
    if _FULL_SHA.match(ref):
        expected = f"sha-{ref[:7]}"
        assert tag == expected, (
            f"library pinned to commit {ref[:7]} but gme-sources runs {tag!r} "
            f"(expected {expected!r})"
        )
        return

    assert tag == ref.lstrip("v"), (
        f"version skew: the library is pinned to {ref!r} (pyproject.toml) but "
        f"the gme-sources image is {tag!r} "
        f"({DEPLOY_COMPOSE.relative_to(REPO_ROOT).as_posix()}). Bump both."
    )


def test_no_install_path_carries_a_second_pin() -> None:
    ref = _library_pin()
    offenders: list[str] = []
    for rel in _INSTALL_PATHS:
        path = REPO_ROOT / rel
        if not path.exists():
            continue
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1,
        ):
            match = _REQUIREMENT.search(line)
            if match is None:
                continue
            found = match.group("ref")
            if found.startswith(("${", "$(")):
                continue  # documented build-arg override, not a pin
            offenders.append(f"{rel}:{lineno}: pins {found!r}")

    assert not offenders, (
        "the child library version must live only in pyproject.toml "
        f"(currently {ref!r}); these paths re-pin it and will drift:\n"
        + "\n".join(offenders)
    )
