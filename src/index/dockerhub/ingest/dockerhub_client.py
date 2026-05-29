"""Direct Docker Hub v2 REST client for the index module.

The public Docker Hub API (`https://hub.docker.com/v2/`) serves public
repository metadata and tags anonymously (rate-limited). A bearer token
(`DOCKERHUB_TOKEN`) is optional and only raises the rate limit.

Endpoints used:

  GET /v2/repositories/{namespace}/{name}        — repo metadata + full_description
  GET /v2/repositories/{namespace}/{name}/tags   — tag list (paginated)

Responses are memoised through the shared `ProviderCache` (same TTL +
SQLite schema as the v2 cache) so a cold re-run reuses data without a
single HTTP call. Errors never raise — callers get ``None`` / ``[]`` and
decide how to proceed.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import requests

from src.v2.ingest.cache import ProviderCache

LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 30


class DockerHubClient:
    """Thin REST client for public Docker Hub metadata with `ProviderCache`."""

    def __init__(
        self,
        *,
        api_base: str = "https://hub.docker.com/v2",
        token: str | None = None,
        cache_path: Path,
    ) -> None:
        self._api_base = api_base.rstrip("/")
        self._token = (token or "").strip() or None
        self._cache = ProviderCache(cache_path)
        if self._token:
            LOGGER.info("dockerhub client: bearer token configured")
        else:
            LOGGER.info("dockerhub client: anonymous (public repos, rate-limited)")

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _get_json(self, url: str) -> Any:
        try:
            response = requests.get(
                url, headers=self._headers(), timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except Exception:  # noqa: BLE001 — client contract is "never raise"; transport errors -> None
            LOGGER.exception("dockerhub GET failed: %s", url)
            return None
        if response.status_code == 404:
            LOGGER.info("dockerhub GET 404: %s", url)
            return None
        if response.status_code != 200:
            LOGGER.warning(
                "dockerhub GET returned %d for %s", response.status_code, url,
            )
            return None
        try:
            return response.json()
        except ValueError:
            LOGGER.exception("dockerhub GET response not JSON: %s", url)
            return None

    def get_repository(self, namespace: str, name: str) -> dict[str, Any] | None:
        """``GET /v2/repositories/{namespace}/{name}`` — metadata or None on 404."""
        url = f"{self._api_base}/repositories/{namespace}/{name}"
        key = ProviderCache.make_key(
            "dockerhub", "get_repository", namespace=namespace, name=name,
        )
        return self._cache.get_or_set(
            key,
            lambda: self._get_json(url),
            label=f"dockerhub.get_repository({namespace}/{name})",
        )

    def get_tags(self, namespace: str, name: str, *, limit: int = 50) -> list[str]:
        """``GET /v2/repositories/{namespace}/{name}/tags`` — tag names, newest
        first, capped at ``limit``. Empty list on any error."""
        page_size = min(max(limit, 1), 100)
        url = (
            f"{self._api_base}/repositories/{namespace}/{name}"
            f"/tags?page_size={page_size}&ordering=last_updated"
        )
        key = ProviderCache.make_key(
            "dockerhub", "get_tags", namespace=namespace, name=name, limit=limit,
        )
        payload = self._cache.get_or_set(
            key,
            lambda: self._get_json(url),
            label=f"dockerhub.get_tags({namespace}/{name})",
        )
        if not isinstance(payload, dict):
            return []
        results = payload.get("results")
        if not isinstance(results, list):
            return []
        tags: list[str] = []
        for entry in results:
            if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                tags.append(entry["name"])
            if len(tags) >= limit:
                break
        return tags
