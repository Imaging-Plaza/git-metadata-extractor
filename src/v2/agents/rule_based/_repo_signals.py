"""Deterministic signal extractors for repository enrichment fields.

These are pure functions (no I/O, no LLM) used by ``repository_agent.py``
to surface ``gme-internal:*`` fields from already-gathered context data
(README text, aux-files, root-listing entries).

Public surface
--------------
parse_test_coverage(readme)  →  "87%" | None
parse_docker_hub_url(readme, aux_files)  →  URL | None
detect_has_ci(root_entries)  →  True | False | None
parse_npm_name(aux_files)  →  "pkg" | "@scope/pkg" | None
parse_pypi_name(aux_files)  →  "project-name" | None
repo_url_matches(candidate_url, full_name)  →  True | False
summarize_registry_package(pkg)  →  flat scalar dict (always-present keys)
"""
from __future__ import annotations

import configparser
import json
import re
from typing import Any

import tomllib

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
    * ``package_tags``             — distinct image tags across all packages
                                     (list → repeated triples). GHCR calls
                                     these *tags* (``metadata.container.tags``).
    * ``latest_package_updated_at`` — most recent ``updated_at`` (ISO 8601)

    Per-package tag detail stays in the raw ``_container_images`` payload.
    The five keys are *always* present (``None`` when there is no package data).
    """
    out: dict[str, Any] = {
        "package_count": None,
        "package_names": None,
        "package_image_refs": None,
        "package_tags": None,
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
    tags = sorted({
        tag
        for c in images
        if isinstance(c.get("tags"), list)
        for tag in c["tags"]
        if isinstance(tag, str) and tag
    })
    out["package_tags"] = tags or None
    updated = sorted(
        c["updated_at"]
        for c in images
        if isinstance(c.get("updated_at"), str) and c["updated_at"]
    )
    if updated:
        out["latest_package_updated_at"] = updated[-1]
    return out


# ---------------------------------------------------------------------------
# npm / PyPI registry package discovery — manifest name parsing + back-ref
# ---------------------------------------------------------------------------

# A normalised GitHub repo path needs at least owner + repo segments.
_OWNER_REPO_SEGMENTS = 2


def _aux_file_lookup(aux_files: Any, *names: str) -> str | None:
    """Return the content of the first of *names* present in *aux_files*
    (case-insensitive on the filename), else None.

    ``aux_files`` keys may include a path prefix (e.g. ``.github/foo``); we
    match on the basename so root manifests are found regardless of casing.
    """
    if not isinstance(aux_files, dict):
        return None
    wanted = {n.lower() for n in names}
    for filename, content in aux_files.items():
        if not isinstance(filename, str) or not isinstance(content, str):
            continue
        base = filename.rsplit("/", maxsplit=1)[-1].lower()
        if base in wanted:
            return content
    return None


def parse_npm_name(aux_files: Any) -> str | None:
    """Extract the published npm package name from a repo's ``package.json``.

    Returns the ``name`` field verbatim (scoped names ``@scope/pkg`` are kept
    intact). Returns None when ``package.json`` is missing, malformed, has no
    ``name``, or declares ``"private": true`` (private packages are never
    published to the public registry).
    """
    content = _aux_file_lookup(aux_files, "package.json")
    if content is None:
        return None
    try:
        data = json.loads(content)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("private") is True:
        return None
    name = data.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def _name_from_table(table: Any) -> str | None:
    """Return a non-empty ``name`` string from a TOML table-like dict."""
    if isinstance(table, dict):
        name = table.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


def _pypi_name_from_pyproject(content: str) -> str | None:
    """Extract the project name from ``pyproject.toml`` content.

    Tries ``[project].name`` (PEP 621) then ``[tool.poetry].name``.
    """
    try:
        data = tomllib.loads(content)
    except (tomllib.TOMLDecodeError, ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    name = _name_from_table(data.get("project"))
    if name:
        return name
    tool = data.get("tool")
    poetry = tool.get("poetry") if isinstance(tool, dict) else None
    return _name_from_table(poetry)


def _pypi_name_from_setup_cfg(content: str) -> str | None:
    """Extract ``[metadata] name`` from ``setup.cfg`` content."""
    parser = configparser.ConfigParser()
    try:
        parser.read_string(content)
    except configparser.Error:
        return None
    if parser.has_option("metadata", "name"):
        name = parser.get("metadata", "name").strip()
        if name:
            return name
    return None


def parse_pypi_name(aux_files: Any) -> str | None:
    """Extract the published PyPI project name from a repo's manifests.

    Order of precedence:
      1. ``pyproject.toml`` → ``[project].name`` (PEP 621)
      2. ``pyproject.toml`` → ``[tool.poetry].name``
      3. ``setup.cfg`` → ``[metadata] name``

    ``setup.py`` is intentionally skipped — executing it is unsafe. The name
    is returned as-declared (no PEP 503 normalisation; PyPI accepts the
    project name as-is). Returns None when no name can be found.
    """
    pyproject = _aux_file_lookup(aux_files, "pyproject.toml")
    if pyproject is not None:
        name = _pypi_name_from_pyproject(pyproject)
        if name:
            return name

    setup_cfg = _aux_file_lookup(aux_files, "setup.cfg")
    if setup_cfg is not None:
        return _pypi_name_from_setup_cfg(setup_cfg)
    return None


def repo_url_matches(candidate_url: Any, full_name: str) -> bool:
    """Return True iff *candidate_url* resolves to ``github.com/<full_name>``.

    Normalises the many shapes a registry's ``repository.url`` can take:
    ``git+https://github.com/o/r.git``, ``ssh://git@github.com/o/r``,
    ``git@github.com:o/r.git`` (scp-like), ``git://github.com/o/r``,
    trailing ``.git`` / ``/``, and arbitrary host casing. Comparison on
    owner/repo is case-insensitive. Returns False for None/empty, non-GitHub
    hosts, or any owner/repo mismatch.
    """
    if not isinstance(candidate_url, str) or not candidate_url.strip():
        return False
    if not isinstance(full_name, str) or "/" not in full_name:
        return False

    url = candidate_url.strip()
    # Strip a leading `git+` VCS-prefix (git+https://…, git+ssh://…).
    if url.lower().startswith("git+"):
        url = url[4:]

    # scp-like syntax: git@github.com:owner/repo(.git)
    scp_match = re.match(
        r"^(?:ssh://)?git@([^/:]+):(.+)$", url, flags=re.IGNORECASE,
    )
    if scp_match:
        host = scp_match.group(1).lower()
        path = scp_match.group(2)
    else:
        # Strip known scheme prefixes, leaving host/path.
        stripped = re.sub(
            r"^(?:https?|ssh|git)://", "", url, flags=re.IGNORECASE,
        )
        # ssh://git@github.com/... — drop a leading userinfo (git@).
        stripped = re.sub(r"^[^/@]+@", "", stripped)
        if "/" not in stripped:
            return False
        host, path = stripped.split("/", maxsplit=1)
        host = host.lower()

    if host not in ("github.com", "www.github.com"):
        # Only github.com (and its www. alias) count — reject lookalike
        # subdomains like evil.github.com that would otherwise false-match.
        return False

    path = path.strip("/")
    if path.lower().endswith(".git"):
        path = path[: -len(".git")]
    path = path.strip("/")
    segments = [seg for seg in path.split("/") if seg]
    if len(segments) < _OWNER_REPO_SEGMENTS:
        return False
    candidate_owner_repo = f"{segments[0]}/{segments[1]}"
    return candidate_owner_repo.lower() == full_name.strip().strip("/").lower()


def summarize_registry_package(pkg: Any) -> dict[str, Any]:
    """Reduce a thin registry-package dict to flat, RDF-friendly scalars.

    Mirrors ``summarize_releases`` / ``summarize_packages``: the keys are
    *always* present (all None when *pkg* is None or not a dict) so the
    splatted ``_npm_*`` / ``_pypi_*`` internal fields are stable. The thin
    dict comes from ``PackageRegistryProvider`` and carries the published
    package's name, latest version, full version list, latest release date,
    registry (human) URL, and the back-reference link policy
    (``verified`` / ``name_only``).

    Emitted keys:
      * ``package``              — published package/project name
      * ``latest_version``       — newest published version string
      * ``versions``             — list of all version strings (or None)
      * ``latest_release_date``  — ISO 8601 date of the latest version
      * ``registry_url``         — human-facing registry page URL
      * ``link``                 — ``"verified"`` | ``"name_only"`` | None
    """
    out: dict[str, Any] = {
        "package": None,
        "latest_version": None,
        "versions": None,
        "latest_release_date": None,
        "registry_url": None,
        "link": None,
    }
    if not isinstance(pkg, dict):
        return out
    name = pkg.get("name")
    if isinstance(name, str) and name:
        out["package"] = name
    latest = pkg.get("latest_version")
    if isinstance(latest, str) and latest:
        out["latest_version"] = latest
    versions = pkg.get("versions")
    if isinstance(versions, list) and versions:
        out["versions"] = [v for v in versions if isinstance(v, str) and v] or None
    date = pkg.get("latest_release_date")
    if isinstance(date, str) and date:
        out["latest_release_date"] = date
    registry_url = pkg.get("registry_url")
    if isinstance(registry_url, str) and registry_url:
        out["registry_url"] = registry_url
    link = pkg.get("link")
    if isinstance(link, str) and link:
        out["link"] = link
    return out
