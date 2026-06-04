# src/index/_gitlab_base/client.py
"""Thin GitLab REST v4 client: per-host base URL, optional token, page
pagination via the X-Next-Page header, and bounded retry on 429/5xx.

`transport` is injectable for tests. One instance per GitLab host.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from collections.abc import Iterator

LOGGER = logging.getLogger(__name__)
_RETRY_STATUS = {429, 500, 502, 503, 504}


class GitLabClient:
    def __init__(  # noqa: PLR0913
        self,
        *,
        host: str,
        token: str | None = None,
        per_page: int = 100,
        timeout: float = 30.0,
        max_retries: int = 4,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base = f"https://{host}/api/v4"
        self._per_page = per_page
        self._max_retries = max_retries
        headers = {"Accept": "application/json"}
        if token:
            headers["PRIVATE-TOKEN"] = token
        self._client = httpx.Client(
            headers=headers, timeout=timeout, transport=transport,
        )

    def _get(self, path: str, params: dict[str, Any]) -> httpx.Response:
        url = f"{self._base}{path}"
        for attempt in range(self._max_retries + 1):
            resp = self._client.get(url, params=params)
            if resp.status_code in _RETRY_STATUS and attempt < self._max_retries:
                wait = float(resp.headers.get("Retry-After") or (2 ** attempt))
                LOGGER.warning("gitlab %s -> %s; retry in %ss", url, resp.status_code, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        return resp  # pragma: no cover

    def _paginate(self, path: str, params: dict[str, Any]) -> Iterator[dict[str, Any]]:
        page = 1
        while page:
            resp = self._get(path, {**params, "per_page": self._per_page, "page": page})
            yield from resp.json()
            nxt = resp.headers.get("X-Next-Page", "").strip()
            page = int(nxt) if nxt else 0

    def iter_public_projects(self) -> Iterator[dict[str, Any]]:
        yield from self._paginate("/projects", {"visibility": "public", "archived": "false"})

    def close(self) -> None:
        self._client.close()
