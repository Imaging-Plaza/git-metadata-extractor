"""Selenium-driven SWISSUbase client.

SWISSUbase exposes a JSON API but every public detail endpoint requires
the session cookie set by ``/api/v2/actions/base`` plus a quirky
``Accept: q=0.8;application/json;q=0.9`` header (without it the server
returns ``403 Not authorized``). Both anonymous curl and ``httpx``
requests therefore fail; only a real browser session works.

We open one persistent ``webdriver.Remote`` session against the shared
Selenium Grid, navigate once to the search page (which seeds cookies and
boots the Angular app), then call the API endpoints over the same
browser via ``driver.execute_async_script(fetch(...))``. The browser
attaches the cookies automatically; we only need to supply the magic
``Accept`` header on each call.

The catalogue list page itself is also rendered via that JSON API
(``POST /api/public/catalogue/catalogue/v1/search-studies/{lang}``), so
we don't scrape the Material table — we ask the API directly for each
page of results, which is much faster than waiting for ``mat-table``
hydration on every page.
"""

from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

if TYPE_CHECKING:
    from collections.abc import Iterator

    from src.index.swissubase.config import SwissubaseIndexConfig

LOGGER = logging.getLogger(__name__)

SWISSUBASE_ACCEPT = "q=0.8;application/json;q=0.9"

# The search-studies endpoint validates pagesize against this set
# server-side; anything else returns 400 "Data sent is not valid".
VALID_PAGESIZES: frozenset[int] = frozenset({5, 10, 20, 50})

_FETCH_GET_JS = """
const url = arguments[0];
const cb = arguments[1];
fetch(url, {
  credentials: 'same-origin',
  headers: {'Accept': 'q=0.8;application/json;q=0.9'},
}).then(r => r.text().then(t => cb({status: r.status, body: t})))
  .catch(e => cb({status: -1, body: String(e)}));
"""

_FETCH_POST_JS = """
const url = arguments[0];
const body = arguments[1];
const cb = arguments[2];
fetch(url, {
  method: 'POST',
  credentials: 'same-origin',
  headers: {
    'Accept': 'q=0.8;application/json;q=0.9',
    'Content-Type': 'application/json',
  },
  body: JSON.stringify(body),
}).then(r => r.text().then(t => cb({status: r.status, body: t})))
  .catch(e => cb({status: -1, body: String(e)}));
"""


class SwissubaseAPIError(RuntimeError):
    """Raised when a swissUbase JSON endpoint returns a non-success status."""


class SwissubaseClient:
    """Browser-backed client for the SWISSUbase public catalogue API.

    Use as a context manager — the underlying ``webdriver.Remote`` is
    quit on exit. Every method retries lightly on transient network
    errors (status -1) but lets HTTP errors bubble up.
    """

    def __init__(self, config: SwissubaseIndexConfig) -> None:
        self._config = config
        self._driver: webdriver.Remote | None = None
        self._session_warm = False

    # ---- Lifecycle -------------------------------------------------------

    def __enter__(self) -> SwissubaseClient:
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        self.close()

    def open(self) -> None:
        if self._driver is not None:
            return
        self._config.require_selenium()
        opts = FirefoxOptions()
        if self._config.selenium.headless:
            opts.add_argument("--headless")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        self._driver = webdriver.Remote(
            command_executor=self._config.selenium.remote_url,
            options=opts,
        )
        self._driver.set_page_load_timeout(self._config.selenium.page_load_timeout_seconds)
        # Bump above detail_timeout_seconds so the inner fetch can hit the
        # 30s limit before the outer script timeout fires; otherwise
        # Selenium throws TimeoutException on the script itself.
        self._driver.set_script_timeout(
            self._config.catalogue.detail_timeout_seconds * 2,
        )

    def close(self) -> None:
        if self._driver is not None:
            try:
                self._driver.quit()
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("driver.quit failed: %s", exc)
            self._driver = None
            self._session_warm = False

    @property
    def driver(self) -> webdriver.Remote:
        if self._driver is None:
            message = "SwissubaseClient not opened — call open() or use as context manager"
            raise RuntimeError(message)
        return self._driver

    def _warm_session(self) -> None:
        """Visit the catalogue once so the browser obtains the session cookies.

        Without this, every subsequent JSON call returns 403.
        """
        if self._session_warm:
            return
        url = (
            f"{self._config.catalogue.base_url}/{self._config.catalogue.language}/catalogue/"
            f"search?q=&p=0&ps=10&sn=ref-number&sd=desc"
        )
        LOGGER.info("warming swissUbase session at %s", url)
        self.driver.get(url)
        try:
            WebDriverWait(
                self.driver,
                self._config.catalogue.list_render_timeout_seconds,
            ).until(EC.presence_of_element_located((By.CSS_SELECTOR, "tr.mat-mdc-row")))
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(
                "session-warm wait timed out (%s); continuing anyway",
                exc,
            )
        self._session_warm = True

    # ---- HTTP helpers ----------------------------------------------------

    def _get_json(self, path: str) -> Any:
        self._warm_session()
        url = self._config.catalogue.base_url + path
        result = self.driver.execute_async_script(_FETCH_GET_JS, url)
        return self._handle_result(url, result)

    def _post_json(self, path: str, body: dict[str, Any]) -> Any:
        self._warm_session()
        url = self._config.catalogue.base_url + path
        result = self.driver.execute_async_script(_FETCH_POST_JS, url, body)
        return self._handle_result(url, result, body=body)

    @staticmethod
    def _handle_result(
        url: str,
        result: dict[str, Any],
        *,
        body: dict[str, Any] | None = None,
    ) -> Any:
        status = int(result.get("status") or 0)
        raw_body = result.get("body") or ""
        if status != 200:
            preview = raw_body[:300] if isinstance(raw_body, str) else str(raw_body)[:300]
            message = (
                f"swissubase API error: status={status} url={url} "
                f"body={preview!r}"
            )
            if body is not None:
                message += f" req={json.dumps(body)[:300]}"
            raise SwissubaseAPIError(message)
        if not raw_body:
            return None
        try:
            return json.loads(raw_body)
        except json.JSONDecodeError as exc:
            message = f"swissubase non-JSON response from {url}: {raw_body[:300]!r}"
            raise SwissubaseAPIError(message) from exc

    # ---- Catalogue endpoints --------------------------------------------

    def search_studies_page(
        self,
        *,
        start: int = 1,
        pagesize: int = 50,
        query_string: str = "",
        facet_filters: dict[str, Any] | None = None,
        sort_name: str = "referenceNumber",
        sort_direction: str = "desc",
    ) -> Any:
        """One page of catalogue search results.

        ``start`` is the 1-indexed item offset (page 2 of pagesize=10
        is ``start=11``; page 1 is ``start=1`` or ``start=0``).
        ``pagesize`` must be in :data:`VALID_PAGESIZES` — the server
        rejects anything else with ``400 Data sent is not valid``.

        Returns the full JSON payload (``{"items": [...], "total":
        N, "facets": [...]}``) — caller extracts what it needs.
        """
        if pagesize not in VALID_PAGESIZES:
            message = (
                f"pagesize={pagesize} is invalid; must be one of "
                f"{sorted(VALID_PAGESIZES)}"
            )
            raise ValueError(message)
        body = {
            "query_string": query_string,
            "facet_filters": facet_filters or {},
            "start": start,
            "pagesize": pagesize,
            "sort": {"name": sort_name, "direction": sort_direction},
            "institutionId": None,
            "personId": None,
        }
        path = (
            f"/api/public/catalogue/catalogue/v1/search-studies/"
            f"{self._config.catalogue.language}"
        )
        return self._post_json(path, body)

    def iter_studies(
        self,
        *,
        pagesize: int | None = None,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield raw catalogue items across pages until exhaustion or limit."""
        size = pagesize or self._config.catalogue.page_size
        if size not in VALID_PAGESIZES:
            message = (
                f"pagesize={size} is invalid; must be one of "
                f"{sorted(VALID_PAGESIZES)}"
            )
            raise ValueError(message)
        start = 1
        yielded = 0
        total: int | None = None
        delay = self._config.catalogue.page_delay_seconds
        while True:
            payload = self.search_studies_page(start=start, pagesize=size)
            items = _extract_items(payload)
            if isinstance(payload, dict) and isinstance(payload.get("total"), int):
                total = payload["total"]
            if not items:
                return
            for item in items:
                yield item
                yielded += 1
                if limit is not None and yielded >= limit:
                    return
            start += len(items)
            if total is not None and start > total:
                return
            if delay > 0:
                time.sleep(delay)

    def fetch_study_overview(self, study_id: str) -> Any:
        path = (
            f"/api/public/catalogue/studies/v1/{study_id}/overview-block/"
            f"{self._config.catalogue.language}"
        )
        return self._get_json(path)

    def iter_studies_by_id(
        self,
        *,
        start_id: int,
        end_id: int,
        skip_ids: set[int] | None = None,
    ) -> Iterator[tuple[int, dict[str, Any]]]:
        """Yield ``(study_id, overview)`` for every existing study in [start_id, end_id].

        ``studyVersionId`` is sparse but bounded — observed range is roughly
        1 to ~21,500 with ~58% density. The search-studies endpoint caps deep
        pagination, so we instead iterate IDs directly: each per-study
        endpoint accepts any valid ID without a window limit.

        Per-ID outcomes:

        - **200** → yield ``(id, overview)``.
        - **404** → study doesn't exist; skip silently.
        - **403** → study exists but isn't public; skip silently.
        - other 4xx/5xx → log and skip (let caller decide on retry policy).

        The default polite spacing between requests reuses
        ``catalogue.page_delay_seconds``.
        """
        skip = skip_ids or set()
        for sid in range(start_id, end_id + 1):
            if sid in skip:
                continue
            try:
                overview = self.fetch_study_overview(str(sid))
            except SwissubaseAPIError as exc:
                msg = str(exc)
                if "status=404" in msg or "status=403" in msg:
                    continue
                LOGGER.warning("overview id=%d failed: %s", sid, msg[:200])
                continue
            except Exception as exc:  # noqa: BLE001
                # Selenium TimeoutException, WebDriverException, transient
                # network errors — keep the ingest moving instead of letting
                # one slow study kill the whole run. Brief sleep to let
                # whatever caused the hiccup settle.
                LOGGER.warning(
                    "overview id=%d transient error (%s: %s); skipping",
                    sid, type(exc).__name__, str(exc)[:200],
                )
                time.sleep(2.0)
                continue
            if isinstance(overview, dict):
                yield sid, overview
            if self._config.catalogue.per_id_delay_seconds > 0:
                time.sleep(self._config.catalogue.per_id_delay_seconds)

    def fetch_study_main(self, study_id: str) -> Any:
        path = (
            f"/api/public/catalogue/studies/v1/{study_id}/main/"
            f"{self._config.catalogue.language}"
        )
        return self._get_json(path)

    def fetch_study_dynamic_blocks(self, study_id: str) -> Any:
        path = f"/api/public/catalogue/studies/v1/{study_id}/dynamic-blocks"
        return self._get_json(path)


def _extract_items(payload: Any) -> list[dict[str, Any]]:
    """Best-effort extractor for the catalogue search response.

    The exact shape isn't formally documented; observed responses use
    ``{"items": [...]}`` or ``{"hits": [...]}`` or ``{"results": [...]}``
    or a top-level list. Try each in order.
    """
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("items", "hits", "results", "data", "studies"):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


@contextmanager
def open_client(config: SwissubaseIndexConfig) -> Iterator[SwissubaseClient]:
    client = SwissubaseClient(config)
    try:
        client.open()
        yield client
    finally:
        client.close()
