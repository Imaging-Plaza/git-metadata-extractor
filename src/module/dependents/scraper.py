"""HTML scraping of GitHub's `/network/dependents` page.

Two layers, deliberately separated:

1. `parse_dependents_page(html)` — pure function from raw HTML to a
   `ParsedDependentsPage`. No I/O. Easy to test against fixtures.
2. `fetch_dependents_html(url)` — Selenium-backed fetch that delegates to
   the existing `_load_html_via_selenium` helper. Has I/O; degrades to
   an empty string on missing `SELENIUM_REMOTE_URL`.

`iterate_dependents(...)` glues them together: walks pages until a cap is
hit or pagination is exhausted, yielding `DependentItem` objects.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from typing import Iterable
from urllib.parse import urlencode

from bs4 import BeautifulSoup, Tag

from src.module.dependents.models import (
    DependentItem,
    DependentKind,
    ParsedDependentsPage,
)

logger = logging.getLogger(__name__)

DEPENDENTS_BASE = "https://github.com"
DEFAULT_PAGE_TIMEOUT_SECONDS = 60
DEFAULT_PAGE_WAIT_SECONDS = 30


# ---------------------------------------------------------------------------
# URL construction
# ---------------------------------------------------------------------------


def build_dependents_url(
    full_name: str,
    *,
    kind: DependentKind = "REPOSITORY",
    cursor: str | None = None,
) -> str:
    """Construct the URL for the dependents page of `<full_name>`.

    `cursor` is the value of GitHub's `dependents_after` query param;
    use the URL embedded in the previous page's `Next` button rather
    than synthesising it.
    """

    if "/" not in full_name:
        message = f"full_name must be 'owner/repo', got {full_name!r}"
        raise ValueError(message)
    params: dict[str, str] = {"dependent_type": kind}
    if cursor:
        params["dependents_after"] = cursor
    return f"{DEPENDENTS_BASE}/{full_name}/network/dependents?{urlencode(params)}"


# ---------------------------------------------------------------------------
# Pure-function HTML parser
# ---------------------------------------------------------------------------

_TOGGLE_PATTERN = re.compile(r"^\s*([\d,]+)\s*(Repositories|Packages)\s*$")


def _parse_int_with_commas(value: str | None) -> int:
    if not isinstance(value, str):
        return 0
    cleaned = value.strip().replace(",", "")
    return int(cleaned) if cleaned.isdigit() else 0


def _extract_toggle_counts(
    soup: BeautifulSoup,
) -> tuple[int, int, DependentKind | None]:
    """Read the `<N> Repositories` and `<N> Packages` totals + which view is selected."""

    repository_count = 0
    package_count = 0
    selected_kind: DependentKind | None = None

    # The toggle bar is two `<a class="btn-link [selected]">` anchors. Each
    # one's visible text reads "<N> Repositories" or "<N> Packages".
    for anchor in soup.find_all("a", class_=lambda c: c and "btn-link" in c.split()):
        text = anchor.get_text(" ", strip=True)
        match = _TOGGLE_PATTERN.match(text)
        if match is None:
            continue
        count = _parse_int_with_commas(match.group(1))
        kind_label = match.group(2)
        if kind_label == "Repositories":
            repository_count = count
        elif kind_label == "Packages":
            package_count = count

        # `class="btn-link selected"` marks the currently-shown view.
        classes = anchor.get("class") or []
        if "selected" in classes:
            selected_kind = "REPOSITORY" if kind_label == "Repositories" else "PACKAGE"

    return repository_count, package_count, selected_kind


def _extract_count_after_icon(row: Tag, icon_class: str) -> int:
    """Return the integer immediately following an octicon-* SVG inside a row.

    Each dependent row carries `<svg class="octicon octicon-star">…</svg> <NUM>`
    and similarly for forks. We locate the SVG and read the next text node.
    """

    svg = row.find("svg", class_=lambda c: c and icon_class in c.split())
    if svg is None:
        return 0
    # The number is the SVG's next non-empty sibling text.
    sibling = svg.next_sibling
    while sibling is not None:
        text = sibling if isinstance(sibling, str) else sibling.get_text(" ", strip=True)
        if isinstance(text, str):
            text = text.strip()
            if text:
                return _parse_int_with_commas(text)
        sibling = getattr(sibling, "next_sibling", None)
    return 0


def _extract_owner_repo(row: Tag) -> tuple[str | None, str | None]:
    """Pull `<owner>` and `<repo>` from a dependent row's `<a class="text-bold">`.

    `href` shape: `/<owner>/<repo>`. Anything else returns (None, None).
    """

    repo_anchor = row.find("a", class_=lambda c: c and "text-bold" in c.split())
    if repo_anchor is None:
        return None, None
    href = repo_anchor.get("href") or ""
    if not isinstance(href, str) or not href.startswith("/"):
        return None, None
    parts = [segment for segment in href.split("/") if segment]
    if len(parts) < 2:
        return None, None
    owner = parts[0]
    # Strip any trailing path/query — we only want owner/repo.
    repo = parts[1].split("?", 1)[0]
    if not owner or not repo:
        return None, None
    return owner, repo


def _extract_next_cursor_url(soup: BeautifulSoup) -> str | None:
    """Return the absolute URL of the `Next` page, or None when at the end."""

    for anchor in soup.find_all("a", class_=lambda c: c and "BtnGroup-item" in c.split()):
        if anchor.get_text(strip=True) == "Next":
            href = anchor.get("href")
            if isinstance(href, str) and href.strip():
                return href.strip()
    return None


def parse_dependents_page(html: str) -> ParsedDependentsPage:
    """Parse one rendered dependents page into structured form.

    Pure function. No I/O. Safe to call on fixture HTML.
    """

    if not isinstance(html, str) or not html:
        return ParsedDependentsPage()

    soup = BeautifulSoup(html, "lxml")
    repo_count, pkg_count, selected = _extract_toggle_counts(soup)

    items: list[DependentItem] = []
    for row in soup.find_all(attrs={"data-test-id": "dg-repo-pkg-dependent"}):
        if not isinstance(row, Tag):
            continue
        owner, repo = _extract_owner_repo(row)
        if owner is None or repo is None:
            continue
        stars = _extract_count_after_icon(row, "octicon-star")
        forks = _extract_count_after_icon(row, "octicon-repo-forked")
        items.append(
            DependentItem(
                full_name=f"{owner}/{repo}",
                owner=owner,
                repo=repo,
                stars=stars,
                forks=forks,
            ),
        )

    return ParsedDependentsPage(
        repository_count=repo_count,
        package_count=pkg_count,
        selected_kind=selected,
        items=items,
        next_cursor_url=_extract_next_cursor_url(soup),
    )


# ---------------------------------------------------------------------------
# Selenium-backed fetcher
# ---------------------------------------------------------------------------


def fetch_dependents_html(
    url: str,
    *,
    timeout_seconds: int = DEFAULT_PAGE_TIMEOUT_SECONDS,
    wait_seconds: int = DEFAULT_PAGE_WAIT_SECONDS,
) -> str:
    """Fetch one dependents page's raw HTML via the project's Selenium grid.

    Returns an empty string on any error (including missing
    `SELENIUM_REMOTE_URL`) — callers should treat empty HTML as a graceful
    degradation, not an exception. We log but do not raise so a single
    bad page doesn't kill an iteration.
    """

    # Lazy import: keeps `parse_dependents_page` runnable in environments
    # without selenium installed (e.g. pure unit-test runs).
    try:
        from src.v2.agents.llm.agent_tools.selenium_fetch import (
            _load_html_via_selenium,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("dependents: selenium helper unavailable: %s", exc)
        return ""

    try:
        html, _final_url, _title = _load_html_via_selenium(
            url,
            timeout_seconds=timeout_seconds,
            wait_seconds=wait_seconds,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("dependents: selenium fetch failed for %s: %s", url, exc)
        return ""
    return html or ""


# ---------------------------------------------------------------------------
# Aggregating iterator
# ---------------------------------------------------------------------------


def iterate_dependents(
    full_name: str,
    *,
    kind: DependentKind = "REPOSITORY",
    max_pages: int = 5,
    max_items: int = 100,
    fetcher: Iterable[str] | None = None,
) -> Iterator[tuple[DependentItem, ParsedDependentsPage]]:
    """Walk the dependents pages of `full_name` and yield items one at a time.

    Stops when **any** of:
    - `max_pages` pages have been processed
    - `max_items` items have been yielded
    - the page returns no `Next` link (end of pagination)
    - a page returns empty HTML (degraded fetch)

    `fetcher` lets tests inject a fixed list of HTML pages instead of
    going through Selenium. When None, uses `fetch_dependents_html`.

    Yields `(item, parsed_page)` tuples so the caller can also see
    page-level metadata (counts, cursor) without a second pass.
    """

    if max_pages <= 0 or max_items <= 0:
        return

    fetcher_iter = iter(fetcher) if fetcher is not None else None

    url: str | None = build_dependents_url(full_name, kind=kind)
    yielded = 0
    pages_visited = 0

    while url is not None and pages_visited < max_pages and yielded < max_items:
        if fetcher_iter is not None:
            try:
                html = next(fetcher_iter)
            except StopIteration:
                return
        else:
            html = fetch_dependents_html(url)
        pages_visited += 1
        if not html:
            return
        page = parse_dependents_page(html)
        for item in page.items:
            if yielded >= max_items:
                return
            yield item, page
            yielded += 1
        url = page.next_cursor_url


__all__ = [
    "DEPENDENTS_BASE",
    "build_dependents_url",
    "fetch_dependents_html",
    "iterate_dependents",
    "parse_dependents_page",
]
