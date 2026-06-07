"""Public package-registry provider (npm + PyPI).

GitHub Packages covers neither public npmjs.com nor PyPI, so to surface a
repo's *published* packages we query the registries directly by the manifest
name and verify the registry's declared repository URL back-references this
repo (see ``context_gather`` for the link policy). This provider only does
the network I/O + thin-dict extraction; name parsing and back-reference
verification are pure helpers in ``_repo_signals.py``.

Both ``get_npm_package`` / ``get_pypi_package`` are **best-effort**: they
return ``None`` on 404 / non-200 / parse / transport error and NEVER raise,
mirroring the github provider's release / container-image fetchers.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import requests

from src.v2.ingest.cache import ProviderCache

logger = logging.getLogger(__name__)

# Cap on the number of version strings carried in the thin dict. A handful of
# long-lived packages publish thousands of versions; the flat
# `gme-internal:*_versions` triple only needs a representative list.
_MAX_VERSIONS = 200

_REQUEST_TIMEOUT_SECONDS = 15

_HTTP_OK = 200
_HTTP_NOT_FOUND = 404

# Descriptive User-Agent — crates.io REQUIRES one (rejects requests without
# it) and it is polite for every registry. Sent on all outbound requests.
_USER_AGENT = (
    "open-pulse-metadata-enricher "
    "(+https://github.com/Imaging-Plaza; package discovery)"
)


class PackageRegistryProvider:
    """Fetch thin published-package metadata from npmjs.com / PyPI.

    ``session`` is any object exposing ``.get(url, timeout=...)`` returning a
    requests-like response (``.status_code``, ``.json()``); it defaults to the
    ``requests`` module so production code hits the live registries and tests
    can inject a canned-response fake with no real network. ``cache`` is the
    standard ``ProviderCache`` (optional) — responses are cached by
    method + package name like the github provider.
    """

    def __init__(
        self,
        *,
        session: Any | None = None,
        cache: ProviderCache | None = None,
    ) -> None:
        self._session = session if session is not None else requests
        self._cache = cache

    # ------------------------------------------------------------------
    # npm
    # ------------------------------------------------------------------

    def get_npm_package(self, name: str) -> dict[str, Any] | None:
        """Fetch thin metadata for npm package *name* (scoped names ok).

        GETs ``https://registry.npmjs.org/<name>`` (scoped ``@scope/pkg`` is
        URL-encoded to ``@scope%2Fpkg``). Returns None on any failure.
        """
        if not isinstance(name, str) or not name.strip():
            return None
        name = name.strip()

        def _fetch() -> dict[str, Any] | None:
            # Scoped names contain a `/` that must be percent-encoded; the
            # leading `@` is left intact (registry expects `@scope%2Fpkg`).
            encoded = quote(name, safe="@")
            url = f"https://registry.npmjs.org/{encoded}"
            payload = self._get_json(url, subject=f"npm:{name}")
            if not isinstance(payload, dict):
                return None
            return self._thin_npm(payload, name)

        return self._cached(_fetch, method="get_npm_package", name=name)

    @staticmethod
    def _thin_npm(payload: dict[str, Any], name: str) -> dict[str, Any] | None:
        pkg_name = payload.get("name")
        pkg_name = pkg_name if isinstance(pkg_name, str) and pkg_name else name

        dist_tags = payload.get("dist-tags")
        latest_version = (
            dist_tags.get("latest") if isinstance(dist_tags, dict) else None
        )
        latest_version = latest_version if isinstance(latest_version, str) else None

        versions_obj = payload.get("versions")
        versions: list[str] = []
        if isinstance(versions_obj, dict):
            versions = sorted(k for k in versions_obj if isinstance(k, str))
        if len(versions) > _MAX_VERSIONS:
            logger.info(
                "npm package %s has %d versions; truncating to %d",
                name, len(versions), _MAX_VERSIONS,
            )
            versions = versions[:_MAX_VERSIONS]

        time_obj = payload.get("time")
        latest_release_date = None
        if isinstance(time_obj, dict) and latest_version:
            candidate = time_obj.get(latest_version)
            latest_release_date = candidate if isinstance(candidate, str) else None

        repository_url = _extract_npm_repo_url(payload, latest_version)

        return {
            "name": pkg_name,
            "latest_version": latest_version,
            "versions": versions or None,
            "latest_release_date": latest_release_date,
            "repository_url": repository_url,
            "registry_url": f"https://www.npmjs.com/package/{pkg_name}",
        }

    # ------------------------------------------------------------------
    # PyPI
    # ------------------------------------------------------------------

    def get_pypi_package(self, name: str) -> dict[str, Any] | None:
        """Fetch thin metadata for PyPI project *name*.

        GETs ``https://pypi.org/pypi/<name>/json``. Returns None on failure.
        """
        if not isinstance(name, str) or not name.strip():
            return None
        name = name.strip()

        def _fetch() -> dict[str, Any] | None:
            encoded = quote(name, safe="")
            url = f"https://pypi.org/pypi/{encoded}/json"
            payload = self._get_json(url, subject=f"pypi:{name}")
            if not isinstance(payload, dict):
                return None
            return self._thin_pypi(payload, name)

        return self._cached(_fetch, method="get_pypi_package", name=name)

    @staticmethod
    def _thin_pypi(payload: dict[str, Any], name: str) -> dict[str, Any] | None:
        info = payload.get("info") if isinstance(payload.get("info"), dict) else {}

        pkg_name = info.get("name")
        pkg_name = pkg_name if isinstance(pkg_name, str) and pkg_name else name

        latest_version = info.get("version")
        latest_version = latest_version if isinstance(latest_version, str) else None

        releases_obj = payload.get("releases")
        versions: list[str] = []
        if isinstance(releases_obj, dict):
            versions = sorted(k for k in releases_obj if isinstance(k, str))
        if len(versions) > _MAX_VERSIONS:
            logger.info(
                "pypi project %s has %d versions; truncating to %d",
                name, len(versions), _MAX_VERSIONS,
            )
            versions = versions[:_MAX_VERSIONS]

        latest_release_date = _extract_pypi_latest_date(
            payload, releases_obj, latest_version,
        )
        repository_url = _extract_pypi_repo_url(info)

        return {
            "name": pkg_name,
            "latest_version": latest_version,
            "versions": versions or None,
            "latest_release_date": latest_release_date,
            "repository_url": repository_url,
            "registry_url": f"https://pypi.org/project/{pkg_name}/",
        }

    # ------------------------------------------------------------------
    # conda / Anaconda
    # ------------------------------------------------------------------

    def get_conda_package(
        self, channel: str, name: str,
    ) -> dict[str, Any] | None:
        """Fetch thin metadata for conda package *channel*/*name*.

        GETs ``https://api.anaconda.org/package/<channel>/<name>``. Returns
        None on any failure. Adds a ``channel`` key to the thin dict.
        """
        if not isinstance(channel, str) or not channel.strip():
            return None
        if not isinstance(name, str) or not name.strip():
            return None
        channel = channel.strip()
        name = name.strip()

        def _fetch() -> dict[str, Any] | None:
            ch = quote(channel, safe="")
            pkg = quote(name, safe="")
            url = f"https://api.anaconda.org/package/{ch}/{pkg}"
            payload = self._get_json(url, subject=f"conda:{channel}/{name}")
            if not isinstance(payload, dict):
                return None
            return self._thin_conda(payload, channel, name)

        return self._cached(
            _fetch, method="get_conda_package", name=f"{channel}/{name}",
        )

    @staticmethod
    def _thin_conda(
        payload: dict[str, Any], channel: str, name: str,
    ) -> dict[str, Any] | None:
        pkg_name = payload.get("name")
        pkg_name = pkg_name if isinstance(pkg_name, str) and pkg_name else name

        latest_version = payload.get("latest_version")
        latest_version = latest_version if isinstance(latest_version, str) else None

        versions_obj = payload.get("versions")
        versions: list[str] = []
        if isinstance(versions_obj, list):
            versions = [v for v in versions_obj if isinstance(v, str) and v]
        if len(versions) > _MAX_VERSIONS:
            logger.info(
                "conda package %s/%s has %d versions; truncating to %d",
                channel, name, len(versions), _MAX_VERSIONS,
            )
            versions = versions[:_MAX_VERSIONS]

        repository_url = _first_str(
            payload.get("dev_url"),
            payload.get("source_git_url"),
            payload.get("html_url"),
        )

        latest_release_date = _max_str(
            f.get("upload_time")
            for f in payload.get("files", [])
            if isinstance(f, dict)
        )

        return {
            "name": pkg_name,
            "latest_version": latest_version,
            "versions": versions or None,
            "latest_release_date": latest_release_date,
            "repository_url": repository_url,
            "registry_url": f"https://anaconda.org/{channel}/{pkg_name}",
            "channel": channel,
        }

    # ------------------------------------------------------------------
    # crates.io
    # ------------------------------------------------------------------

    def get_crates_package(self, name: str) -> dict[str, Any] | None:
        """Fetch thin metadata for crates.io crate *name*.

        GETs ``https://crates.io/api/v1/crates/<name>``. Returns None on
        failure. crates.io REQUIRES a User-Agent header (sent by _get_json).
        """
        if not isinstance(name, str) or not name.strip():
            return None
        name = name.strip()

        def _fetch() -> dict[str, Any] | None:
            encoded = quote(name, safe="")
            url = f"https://crates.io/api/v1/crates/{encoded}"
            payload = self._get_json(url, subject=f"crates:{name}")
            if not isinstance(payload, dict):
                return None
            return self._thin_crates(payload, name)

        return self._cached(_fetch, method="get_crates_package", name=name)

    @staticmethod
    def _thin_crates(payload: dict[str, Any], name: str) -> dict[str, Any] | None:
        crate = payload.get("crate") if isinstance(payload.get("crate"), dict) else {}

        pkg_name = crate.get("name")
        pkg_name = pkg_name if isinstance(pkg_name, str) and pkg_name else name

        latest_version = _first_str(
            crate.get("max_stable_version"), crate.get("newest_version"),
        )

        versions_obj = payload.get("versions")
        versions: list[str] = []
        if isinstance(versions_obj, list):
            versions = [
                v["num"]
                for v in versions_obj
                if isinstance(v, dict) and isinstance(v.get("num"), str) and v["num"]
            ]
        if len(versions) > _MAX_VERSIONS:
            logger.info(
                "crate %s has %d versions; truncating to %d",
                name, len(versions), _MAX_VERSIONS,
            )
            versions = versions[:_MAX_VERSIONS]

        repository_url = _first_str(crate.get("repository"))
        latest_release_date = _first_str(crate.get("updated_at"))

        return {
            "name": pkg_name,
            "latest_version": latest_version,
            "versions": versions or None,
            "latest_release_date": latest_release_date,
            "repository_url": repository_url,
            "registry_url": f"https://crates.io/crates/{pkg_name}",
        }

    # ------------------------------------------------------------------
    # RubyGems
    # ------------------------------------------------------------------

    def get_rubygems_package(self, name: str) -> dict[str, Any] | None:
        """Fetch thin metadata for RubyGems gem *name*.

        GETs ``https://rubygems.org/api/v1/gems/<name>.json`` and, best-effort,
        ``https://rubygems.org/api/v1/versions/<name>.json`` for the version
        list. Returns None on failure of the primary request.
        """
        if not isinstance(name, str) or not name.strip():
            return None
        name = name.strip()

        def _fetch() -> dict[str, Any] | None:
            encoded = quote(name, safe="")
            url = f"https://rubygems.org/api/v1/gems/{encoded}.json"
            payload = self._get_json(url, subject=f"rubygems:{name}")
            if not isinstance(payload, dict):
                return None
            versions_url = (
                f"https://rubygems.org/api/v1/versions/{encoded}.json"
            )
            versions_payload = self._get_json(
                versions_url, subject=f"rubygems-versions:{name}",
            )
            return self._thin_rubygems(payload, versions_payload, name)

        return self._cached(_fetch, method="get_rubygems_package", name=name)

    @staticmethod
    def _thin_rubygems(
        payload: dict[str, Any], versions_payload: Any, name: str,
    ) -> dict[str, Any] | None:
        pkg_name = payload.get("name")
        pkg_name = pkg_name if isinstance(pkg_name, str) and pkg_name else name

        latest_version = payload.get("version")
        latest_version = latest_version if isinstance(latest_version, str) else None

        repository_url = _first_str(
            payload.get("source_code_uri"), payload.get("homepage_uri"),
        )

        versions: list[str] = []
        latest_release_date: str | None = None
        if isinstance(versions_payload, list):
            versions = [
                v["number"]
                for v in versions_payload
                if isinstance(v, dict)
                and isinstance(v.get("number"), str)
                and v["number"]
            ]
            if len(versions) > _MAX_VERSIONS:
                logger.info(
                    "gem %s has %d versions; truncating to %d",
                    name, len(versions), _MAX_VERSIONS,
                )
                versions = versions[:_MAX_VERSIONS]
            latest_release_date = _max_str(
                v.get("created_at")
                for v in versions_payload
                if isinstance(v, dict)
            )

        return {
            "name": pkg_name,
            "latest_version": latest_version,
            "versions": versions or None,
            "latest_release_date": latest_release_date,
            "repository_url": repository_url,
            "registry_url": f"https://rubygems.org/gems/{pkg_name}",
        }

    def get_go_module(self, module: str) -> dict[str, Any] | None:
        """Fetch thin metadata for Go *module* from the module proxy.

        GETs ``https://proxy.golang.org/<esc>/@latest`` (JSON ``{Version,Time}``)
        and ``/@v/list`` (newline-separated versions). ``<esc>`` lowercases
        capitals via ``!`` escaping (proxy requirement). ``repository_url`` is
        derived from the module path for github-hosted modules so the
        back-reference check works; non-github (vanity) paths yield None →
        ``name_only``. Returns None when the proxy knows nothing about it.
        """
        if not isinstance(module, str) or not module.strip():
            return None
        module = module.strip()

        def _fetch() -> dict[str, Any] | None:
            return self._thin_go(module)

        return self._cached(_fetch, method="get_go_module", name=module)

    def _thin_go(self, module: str) -> dict[str, Any] | None:
        escaped = _go_escape(module)
        latest = self._get_json(
            f"https://proxy.golang.org/{escaped}/@latest", subject=f"go:{module}",
        )
        latest_version = None
        latest_release_date = None
        if isinstance(latest, dict):
            latest_version = _first_str(latest.get("Version"))
            latest_release_date = _first_str(latest.get("Time"))

        list_text = self._get_text(
            f"https://proxy.golang.org/{escaped}/@v/list", subject=f"go-list:{module}",
        )
        versions: list[str] = []
        if isinstance(list_text, str):
            versions = sorted(
                line.strip() for line in list_text.splitlines() if line.strip()
            )
            if len(versions) > _MAX_VERSIONS:
                logger.info(
                    "go module %s has %d versions; truncating to %d",
                    module, len(versions), _MAX_VERSIONS,
                )
                versions = versions[:_MAX_VERSIONS]

        if latest_version is None and not versions:
            # Proxy has no record of this module.
            return None

        return {
            "name": module,
            "latest_version": latest_version,
            "versions": versions or None,
            "latest_release_date": latest_release_date,
            "repository_url": _go_repository_url(module),
            "registry_url": f"https://pkg.go.dev/{module}",
        }

    def get_maven_package(
        self, group_id: str, artifact_id: str,
    ) -> dict[str, Any] | None:
        """Fetch thin metadata for a Maven Central artifact ``group:artifact``.

        Uses the Maven Central Solr API: one query for ``latestVersion`` +
        latest ``timestamp``, one ``core=gav`` query for the version list.
        ``repository_url`` is left None — the Solr index does not expose the
        POM ``<scm>``; the back-reference is established by ``context_gather``
        from the repo's own ``pom.xml`` (see the link policy there).
        """
        if not (isinstance(group_id, str) and group_id.strip()):
            return None
        if not (isinstance(artifact_id, str) and artifact_id.strip()):
            return None
        group_id, artifact_id = group_id.strip(), artifact_id.strip()

        def _fetch() -> dict[str, Any] | None:
            base = "https://search.maven.org/solrsearch/select"
            q = quote(f'g:"{group_id}" AND a:"{artifact_id}"', safe="")
            subject = f"maven:{group_id}:{artifact_id}"
            latest = self._get_json(
                f"{base}?q={q}&rows=1&wt=json", subject=subject,
            )
            docs = _solr_docs(latest)
            if not docs:
                return None
            head = docs[0]
            versions_payload = self._get_json(
                f"{base}?q={q}&core=gav&rows={_MAX_VERSIONS}&wt=json",
                subject=f"{subject}:gav",
            )
            return self._thin_maven(
                group_id, artifact_id, head, _solr_docs(versions_payload),
            )

        return self._cached(
            _fetch, method="get_maven_package", name=f"{group_id}:{artifact_id}",
        )

    @staticmethod
    def _thin_maven(
        group_id: str,
        artifact_id: str,
        head: dict[str, Any],
        version_docs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        latest_version = _first_str(head.get("latestVersion"))
        latest_release_date = _ms_to_iso(head.get("timestamp"))
        versions = [
            d["v"] for d in version_docs
            if isinstance(d, dict) and isinstance(d.get("v"), str) and d["v"]
        ]
        return {
            "name": f"{group_id}:{artifact_id}",
            "group_id": group_id,
            "artifact_id": artifact_id,
            "latest_version": latest_version,
            "versions": versions or None,
            "latest_release_date": latest_release_date,
            "repository_url": None,
            "registry_url": (
                f"https://central.sonatype.com/artifact/{group_id}/{artifact_id}"
            ),
        }

    def get_nuget_package(self, package_id: str) -> dict[str, Any] | None:
        """Fetch thin metadata for a NuGet package *package_id*.

        Search endpoint → canonical id + latest stable version; flat-container
        → the full version list; registration → ``repository`` URL + latest
        ``published`` date. ``repository_url`` is taken ONLY from the package's
        Source-Link ``repository`` (NOT ``projectUrl``, which is usually a
        homepage) so the back-reference check never false-drops on a homepage.
        """
        if not (isinstance(package_id, str) and package_id.strip()):
            return None
        package_id = package_id.strip()

        def _fetch() -> dict[str, Any] | None:
            subject = f"nuget:{package_id}"
            search = self._get_json(
                "https://azuresearch-usnc.nuget.org/query"
                f"?q=packageid:{quote(package_id, safe='')}&take=1",
                subject=subject,
            )
            data = search.get("data") if isinstance(search, dict) else None
            if not (isinstance(data, list) and data and isinstance(data[0], dict)):
                return None
            head = data[0]
            name = _first_str(head.get("id")) or package_id
            latest_version = _first_str(head.get("version"))

            id_lower = name.lower()
            flat = self._get_json(
                f"https://api.nuget.org/v3-flatcontainer/{quote(id_lower, safe='')}"
                "/index.json",
                subject=f"{subject}:versions",
            )
            versions: list[str] = []
            if isinstance(flat, dict) and isinstance(flat.get("versions"), list):
                versions = [v for v in flat["versions"] if isinstance(v, str) and v]
                if len(versions) > _MAX_VERSIONS:
                    logger.info(
                        "nuget package %s has %d versions; truncating to %d",
                        name, len(versions), _MAX_VERSIONS,
                    )
                    versions = versions[:_MAX_VERSIONS]

            repository_url, published = self._nuget_repo_and_date(
                id_lower, latest_version, subject=subject,
            )

            return {
                "name": name,
                "latest_version": latest_version,
                "versions": versions or None,
                "latest_release_date": published,
                "repository_url": repository_url,
                "registry_url": f"https://www.nuget.org/packages/{name}",
            }

        return self._cached(_fetch, method="get_nuget_package", name=package_id)

    def _nuget_repo_and_date(
        self, id_lower: str, target_version: str | None, *, subject: str,
    ) -> tuple[str | None, str | None]:
        """Best-effort (repository_url, published) for *target_version* from the
        NuGet registration index. Handles inline leaves and one paged sub-page;
        returns (None, None) when neither is available."""
        idx = self._get_json(
            "https://api.nuget.org/v3/registration5-gz-semver2/"
            f"{quote(id_lower, safe='')}/index.json",
            subject=f"{subject}:registration",
        )
        pages = idx.get("items") if isinstance(idx, dict) else None
        if not (isinstance(pages, list) and pages):
            return (None, None)
        last = pages[-1]
        leaves = last.get("items") if isinstance(last, dict) else None
        if not isinstance(leaves, list):
            page_url = last.get("@id") if isinstance(last, dict) else None
            if isinstance(page_url, str):
                sub = self._get_json(page_url, subject=f"{subject}:registration-page")
                leaves = sub.get("items") if isinstance(sub, dict) else None
        if not isinstance(leaves, list):
            return (None, None)

        entry = _nuget_catalog_entry(leaves, target_version)
        if entry is None:
            return (None, None)
        repo = entry.get("repository")
        repo_url = None
        if isinstance(repo, dict):
            repo_url = _first_str(repo.get("url"))
        elif isinstance(repo, str):
            repo_url = _first_str(repo)
        published = _first_str(entry.get("published"))
        return (repo_url, published)

    # ------------------------------------------------------------------
    # shared helpers
    # ------------------------------------------------------------------

    def _get_json(self, url: str, *, subject: str) -> Any:
        """GET *url* and return parsed JSON, or None on any failure.

        Sends a descriptive ``User-Agent`` header on every request (crates.io
        rejects requests without one).
        """
        try:
            response = self._session.get(
                url,
                timeout=_REQUEST_TIMEOUT_SECONDS,
                headers={"User-Agent": _USER_AGENT},
            )
        except Exception:  # noqa: BLE001 — best-effort; never raise on transport error.
            logger.info("package registry fetch failed: %s", subject)
            return None
        status = getattr(response, "status_code", None)
        if status == _HTTP_NOT_FOUND:
            return None
        if status != _HTTP_OK:
            logger.info(
                "package registry fetch returned %s for %s", status, subject,
            )
            return None
        try:
            return response.json()
        except ValueError:
            logger.info("package registry response not JSON: %s", subject)
            return None

    def _get_text(self, url: str, *, subject: str) -> str | None:
        """GET *url* and return the body as text, or None on failure.

        For endpoints that return plain text rather than JSON (the Go module
        proxy's ``@v/list``). Same best-effort contract as ``_get_json``.
        """
        try:
            response = self._session.get(
                url,
                timeout=_REQUEST_TIMEOUT_SECONDS,
                headers={"User-Agent": _USER_AGENT},
            )
        except Exception:  # noqa: BLE001 — best-effort; never raise on transport error.
            logger.info("package registry fetch failed: %s", subject)
            return None
        if getattr(response, "status_code", None) != _HTTP_OK:
            return None
        text = getattr(response, "text", None)
        return text if isinstance(text, str) else None

    def _cached(
        self,
        factory: Any,
        *,
        method: str,
        name: str,
    ) -> dict[str, Any] | None:
        if self._cache is None:
            return factory()
        key = ProviderCache.make_key("package_registry", method, name=name)
        return self._cache.get_or_set(
            key, factory, label=f"package_registry.{method}({name})",
        )


def _first_str(*candidates: Any) -> str | None:
    """Return the first non-empty stripped string in *candidates*, else None."""
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _solr_docs(payload: Any) -> list[dict[str, Any]]:
    """Return the ``response.docs`` list from a Maven Central Solr payload."""
    if not isinstance(payload, dict):
        return []
    response = payload.get("response")
    if not isinstance(response, dict):
        return []
    docs = response.get("docs")
    return [d for d in docs if isinstance(d, dict)] if isinstance(docs, list) else []


def _nuget_catalog_entry(
    leaves: list[Any], target_version: str | None,
) -> dict[str, Any] | None:
    """Return the ``catalogEntry`` for *target_version* among registration
    *leaves* (else the last leaf's entry)."""
    last_entry: dict[str, Any] | None = None
    for leaf in leaves:
        entry = leaf.get("catalogEntry") if isinstance(leaf, dict) else None
        if not isinstance(entry, dict):
            continue
        last_entry = entry
        if target_version and entry.get("version") == target_version:
            return entry
    return last_entry


def _ms_to_iso(value: Any) -> str | None:
    """Convert a millisecond epoch (Maven ``timestamp``) to an ISO 8601 UTC
    string, or None when the value isn't a usable number."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def _go_escape(module: str) -> str:
    """Escape a Go module path for the proxy: each uppercase letter X → !x.

    The Go module proxy lowercases capitals via ``!`` escaping to keep paths
    case-insensitive on case-insensitive filesystems
    (``github.com/BurntSushi/toml`` → ``github.com/!burnt!sushi/toml``).
    """
    return "".join(f"!{c.lower()}" if c.isupper() else c for c in module)


def _go_repository_url(module: str) -> str | None:
    """Derive the source repo URL from a github-hosted module path.

    ``github.com/owner/repo[/v2][/sub]`` → ``https://github.com/owner/repo``.
    Non-github (vanity-import) paths return None (no reliable repo mapping
    without resolving go-import meta tags), so they fall back to ``name_only``.
    """
    lower = module.lower()
    if not (lower == "github.com" or lower.startswith("github.com/")):
        return None
    segments = [s for s in module.split("/") if s]
    if len(segments) < _GO_GITHUB_MIN_SEGMENTS:
        return None
    return f"https://{segments[0]}/{segments[1]}/{segments[2]}"


# github.com + owner + repo
_GO_GITHUB_MIN_SEGMENTS = 3


def _max_str(values: Any) -> str | None:
    """Return the lexically-max non-empty string in *values*, else None.

    Used for ISO 8601 timestamps where lexical order == chronological order.
    """
    strings = sorted(v for v in values if isinstance(v, str) and v)
    return strings[-1] if strings else None


def _extract_npm_repo_url(
    payload: dict[str, Any], latest_version: str | None,
) -> str | None:
    """Pull ``repository.url`` from the top-level doc, else from the latest
    version's manifest. Handles both the object form (``{"url": ...}``) and
    the bare-string form npm allows."""
    candidate = _repo_field_to_url(payload.get("repository"))
    if candidate:
        return candidate
    versions_obj = payload.get("versions")
    if isinstance(versions_obj, dict) and latest_version:
        version_doc = versions_obj.get(latest_version)
        if isinstance(version_doc, dict):
            return _repo_field_to_url(version_doc.get("repository"))
    return None


def _repo_field_to_url(repository: Any) -> str | None:
    if isinstance(repository, str) and repository.strip():
        return repository.strip()
    if isinstance(repository, dict):
        url = repository.get("url")
        if isinstance(url, str) and url.strip():
            return url.strip()
    return None


# PyPI `project_urls` keys to consult for the source repo, in priority order.
_PYPI_PROJECT_URL_KEYS = ("Source", "Repository", "Code", "Homepage")


def _extract_pypi_repo_url(info: dict[str, Any]) -> str | None:
    project_urls = info.get("project_urls")
    if isinstance(project_urls, dict):
        # Case-insensitive match on the priority keys (publishers vary the
        # casing: "Source Code", "repository", etc.).
        lowered = {
            k.lower(): v
            for k, v in project_urls.items()
            if isinstance(k, str) and isinstance(v, str) and v.strip()
        }
        for key in _PYPI_PROJECT_URL_KEYS:
            value = lowered.get(key.lower())
            if value:
                return value.strip()
    home_page = info.get("home_page")
    if isinstance(home_page, str) and home_page.strip():
        return home_page.strip()
    return None


def _extract_pypi_latest_date(
    payload: dict[str, Any],
    releases_obj: Any,
    latest_version: str | None,
) -> str | None:
    """Return the max ``upload_time_iso_8601`` for the latest release's files,
    falling back to the top-level ``urls`` block."""

    def _max_upload_time(files: Any) -> str | None:
        if not isinstance(files, list):
            return None
        times = sorted(
            f["upload_time_iso_8601"]
            for f in files
            if isinstance(f, dict)
            and isinstance(f.get("upload_time_iso_8601"), str)
            and f["upload_time_iso_8601"]
        )
        return times[-1] if times else None

    if isinstance(releases_obj, dict) and latest_version:
        date = _max_upload_time(releases_obj.get(latest_version))
        if date:
            return date
    return _max_upload_time(payload.get("urls"))
