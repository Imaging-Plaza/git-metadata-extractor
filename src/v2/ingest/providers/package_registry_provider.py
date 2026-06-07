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
    # shared helpers
    # ------------------------------------------------------------------

    def _get_json(self, url: str, *, subject: str) -> Any:
        """GET *url* and return parsed JSON, or None on any failure."""
        try:
            response = self._session.get(url, timeout=_REQUEST_TIMEOUT_SECONDS)
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
