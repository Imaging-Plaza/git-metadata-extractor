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


# ---------------------------------------------------------------------------
# extract_doc_candidate_urls
# ---------------------------------------------------------------------------

# Patterns for documentation-hosting URLs:
#   1. *.readthedocs.io  (any sub-path)
#   2. *.github.io / *.gitlab.io  (GitHub/GitLab Pages)
#   3. *.gitbook.io
#   4. docs.<domain>  (e.g. docs.myproject.io)
#   5. <any-host>/docs/<path>  (project site with /docs/ path)
_DOC_URL_RE = re.compile(
    r"https?://"
    r"(?:"
    r"[\w.-]+\.readthedocs\.io"         # readthedocs.io
    r"|[\w.-]+\.(?:github|gitlab)\.io"  # GitHub/GitLab Pages
    r"|[\w.-]+\.gitbook\.io"            # GitBook
    r"|docs\.[\w.-]+"                   # docs.* subdomain
    r"|[\w.-]+/docs/[\w./?#=&%-]*"      # /docs/ path (project site)
    r")"
    r"[^\s\"'<>]*",                     # rest of URL until whitespace/quote/tag
    re.IGNORECASE,
)


def extract_doc_candidate_urls(readme: str | None) -> list[str]:
    """Extract documentation-hosting URLs from *readme*.

    Matches readthedocs.io, GitHub/GitLab Pages (*.github.io / *.gitlab.io),
    GitBook (*.gitbook.io), docs.<domain> subdomains, and /docs/ paths on
    project sites.  Returns a de-duped, order-preserving list (may be empty).
    """
    if not isinstance(readme, str) or not readme:
        return []

    seen: set[str] = set()
    result: list[str] = []
    for match in _DOC_URL_RE.finditer(readme):
        url = match.group(0).rstrip(".,;:)")  # strip common trailing punctuation
        if url not in seen:
            seen.add(url)
            result.append(url)
    return result


# ---------------------------------------------------------------------------
# Release frequency — flat scalars for `gme-internal:*`
# ---------------------------------------------------------------------------


def summarize_releases(releases: Any) -> dict[str, Any]:
    """Reduce a raw release list to flat, RDF-friendly scalars.

    The raw ``_releases`` list-of-objects (``{tag_name, published_at, …}``)
    collapses to empty blank nodes on JSON-LD expansion (its inner keys are
    not mapped in the ``@context``), so it is unusable as triples. This
    derives the three flat values a "Release Frequency" consumer actually
    needs — each emits as a single ``gme-internal:`` literal triple:

    * ``release_count``       — number of releases
    * ``first_release_date``  — earliest ``published_at`` (ISO 8601 string)
    * ``latest_release_date`` — latest ``published_at`` (ISO 8601 string)

    ``published_at`` is an ISO 8601 timestamp, so min/max is a plain string
    compare (lexical == chronological). Releases missing ``published_at``
    (e.g. drafts) are ignored for the date bounds but still counted. The
    three keys are *always* present (``None`` when there is no release data),
    mirroring the always-present ``_latest_version`` sibling.
    """
    out: dict[str, Any] = {
        "release_count": None,
        "first_release_date": None,
        "latest_release_date": None,
    }
    if not isinstance(releases, list):
        return out
    out["release_count"] = len(releases)
    dates = sorted(
        r.get("published_at")
        for r in releases
        if isinstance(r, dict) and isinstance(r.get("published_at"), str) and r.get("published_at")
    )
    if dates:
        out["first_release_date"] = dates[0]
        out["latest_release_date"] = dates[-1]
    return out


def summarize_packages(container_images: Any) -> dict[str, Any]:
    """Reduce the raw GHCR container-package list to flat, RDF-friendly scalars.

    Like ``_releases``, the raw ``_container_images`` list-of-objects
    (``{name, image, tags, updated_at, …}``) collapses to empty blank nodes on
    JSON-LD expansion (inner keys unmapped in the ``@context``), so it is
    unusable as triples. This derives the flat values a "Container
    distribution" / package consumer needs, each emitting clean
    ``gme-internal:`` triples:

    * ``package_count``            — number of linked container packages (int)
    * ``package_names``            — package names (list → repeated triples)
    * ``package_image_refs``       — pullable ``ghcr.io/owner/name`` refs (list)
    * ``package_versions``         — distinct version tags across all packages
                                     (list → repeated triples)
    * ``latest_package_updated_at`` — most recent ``updated_at`` (ISO 8601)

    Per-package version detail stays in the raw ``_container_images`` payload.
    The five keys are *always* present (``None`` when there is no package data).
    """
    out: dict[str, Any] = {
        "package_count": None,
        "package_names": None,
        "package_image_refs": None,
        "package_versions": None,
        "latest_package_updated_at": None,
    }
    if not isinstance(container_images, list):
        return out
    images = [c for c in container_images if isinstance(c, dict)]
    out["package_count"] = len(images)
    names = [c["name"] for c in images if isinstance(c.get("name"), str) and c["name"]]
    refs = [c["image"] for c in images if isinstance(c.get("image"), str) and c["image"]]
    out["package_names"] = names or None
    out["package_image_refs"] = refs or None
    versions = sorted({
        tag
        for c in images
        if isinstance(c.get("tags"), list)
        for tag in c["tags"]
        if isinstance(tag, str) and tag
    })
    out["package_versions"] = versions or None
    updated = sorted(
        c["updated_at"]
        for c in images
        if isinstance(c.get("updated_at"), str) and c["updated_at"]
    )
    if updated:
        out["latest_package_updated_at"] = updated[-1]
    return out
