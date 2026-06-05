"""Deterministic signal extractors for repository enrichment fields.

These are pure functions (no I/O, no LLM) used by ``repository_agent.py``
to surface ``gme-internal:*`` fields from already-gathered context data
(README text, aux-files, root-listing entries).

Public surface
--------------
parse_test_coverage(readme)  →  "87%" | None
parse_docker_hub_url(readme, aux_files)  →  URL | None
detect_has_ci(root_entries)  →  True | False | None
"""
from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# _has_ci detection
# ---------------------------------------------------------------------------

# Root-level names (case-insensitive) that indicate a CI/CD configuration.
_CI_INDICATORS: frozenset[str] = frozenset({
    ".github",
    ".gitlab-ci.yml",
    ".travis.yml",
    ".circleci",
    "azure-pipelines.yml",
    "jenkinsfile",
    ".drone.yml",
    "bitbucket-pipelines.yml",
    ".woodpecker.yml",
})


def detect_has_ci(root_entries: list[str] | None) -> bool | None:
    """Return True if any known CI indicator is in *root_entries*, False if
    the list is available but empty / contains no CI files, or None when
    the listing was not available (caller passed None).

    Comparison is case-insensitive so repos that capitalise their
    dotfiles are handled correctly.
    """
    if root_entries is None:
        return None
    for name in root_entries:
        if isinstance(name, str) and name.lower() in _CI_INDICATORS:
            return True
    return False


# ---------------------------------------------------------------------------
# parse_test_coverage
# ---------------------------------------------------------------------------

# (a) Static shields.io badge embedding the number:
#   https://img.shields.io/badge/coverage-87%25-green   (%25 is URL-encoded %)
#   https://img.shields.io/badge/coverage-87%-green     (literal %)
#   alt-text form: coverage-87%
_SHIELDS_COVERAGE_RE = re.compile(
    r"coverage[-_](\d+)(?:%25|%)",
    re.IGNORECASE,
)

# (b) Plain-text: "coverage: 87%" / "test coverage: 87%"
_TEXT_COVERAGE_RE = re.compile(
    r"(?:test\s+)?coverage\s*:\s*(\d+)\s*%",
    re.IGNORECASE,
)


def parse_test_coverage(readme: str | None) -> str | None:
    """Extract a static test-coverage percentage from *readme*.

    Returns the percentage as a string like ``"87%"`` when found, or
    ``None`` when no static number appears in the README (dynamic
    badges — codecov/coveralls URLs without an embedded number — also
    return ``None``).

    Search order:
    1. shields.io badge with embedded number (``coverage-87%25`` or
       ``coverage-87%`` in the image URL or alt-text).
    2. Plain-text pattern (``coverage: 87%`` / ``test coverage: 87%``).
    """
    if not isinstance(readme, str) or not readme:
        return None

    m = _SHIELDS_COVERAGE_RE.search(readme)
    if m:
        return f"{m.group(1)}%"

    m = _TEXT_COVERAGE_RE.search(readme)
    if m:
        return f"{m.group(1)}%"

    return None


# ---------------------------------------------------------------------------
# parse_docker_hub_url
# ---------------------------------------------------------------------------

# Direct Docker Hub URL: https://hub.docker.com/r/<ns>/<name>
_DOCKERHUB_URL_RE = re.compile(
    r"https://hub\.docker\.com/r/([\w.-]+/[\w.-]+?)(?:[/\s\"')>]|$)",
    re.IGNORECASE,
)

# docker pull <ns>/<name>[:<tag>]
# docker run [opts] <ns>/<name>[:<tag>]
_DOCKER_PULL_RUN_RE = re.compile(
    r"docker\s+(?:pull\s+|run\s+(?:\S+\s+)*)([\w.-]+/[\w.-]+?)(?::[^\s\"'`]+)?(?:\s|$|[\"'`])",
    re.IGNORECASE,
)

# Compose image: line: "image: ns/name[:tag]"
_COMPOSE_IMAGE_RE = re.compile(
    r"^\s*image\s*:\s*([\w.-]+/[\w.-]+?)(?::[^\s]+)?\s*$",
    re.MULTILINE | re.IGNORECASE,
)


def _is_official_image(namespace: str) -> bool:
    """Return True for single-component names (no slash) — Docker Hub
    official / library images that don't have a proper namespace."""
    return "/" not in namespace


def _canonicalize(ns_name: str) -> str:
    """Return the canonical hub.docker.com URL for *ns_name* (``ns/repo``)."""
    return f"https://hub.docker.com/r/{ns_name}"


def _docker_hub_url_from_readme(readme: str) -> str | None:
    """Return the first Docker Hub URL found in *readme*, or None."""
    m = _DOCKERHUB_URL_RE.search(readme)
    if m:
        ns_name = m.group(1).rstrip("/")
        if not _is_official_image(ns_name):
            return f"https://hub.docker.com/r/{ns_name}"
    for m in _DOCKER_PULL_RUN_RE.finditer(readme):
        ns_name = m.group(1).rstrip("/")
        if not _is_official_image(ns_name):
            return _canonicalize(ns_name)
    return None


def _docker_hub_url_from_aux_files(aux_files: dict[str, Any]) -> str | None:
    """Return the first Docker Hub image ref found in docker/compose aux-files."""
    for filename, content in aux_files.items():
        if not isinstance(filename, str) or not isinstance(content, str):
            continue
        lower = filename.lower()
        if "docker" in lower or "compose" in lower or lower == "dockerfile":
            for m in _COMPOSE_IMAGE_RE.finditer(content):
                ns_name = m.group(1).rstrip("/")
                if not _is_official_image(ns_name):
                    return _canonicalize(ns_name)
    return None


def parse_docker_hub_url(readme: str | None, aux_files: dict[str, Any] | None) -> str | None:
    """Extract a Docker Hub URL from *readme* and, optionally, aux-files.

    Tries (in order):
    1. Direct ``https://hub.docker.com/r/<ns>/<name>`` link in readme.
    2. ``docker pull <ns>/<name>`` or ``docker run … <ns>/<name>`` in readme.
    3. ``image: <ns>/<name>`` in a Dockerfile/compose file in *aux_files*.

    Returns the canonical ``https://hub.docker.com/r/<ns>/<name>`` form,
    or ``None`` when nothing confident is found.  Official images without
    a namespace (e.g. ``docker pull python``) are skipped.
    """
    if isinstance(readme, str) and readme:
        result = _docker_hub_url_from_readme(readme)
        if result:
            return result
    if isinstance(aux_files, dict):
        return _docker_hub_url_from_aux_files(aux_files)
    return None
