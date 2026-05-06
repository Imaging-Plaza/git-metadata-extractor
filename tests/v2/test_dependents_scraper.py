"""Pure-function parser tests against captured fixtures.

The fixtures under `tests/v2/fixtures/github/dependents/` were captured
via Selenium on 2026-05-01. See `SELECTORS.md` in that directory for the
documented selectors and the expected counts each fixture exercises.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.module.dependents.scraper import (
    build_dependents_url,
    iterate_dependents,
    parse_dependents_page,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "github" / "dependents"


def _load(name: str) -> str:
    return (FIXTURE_DIR / name).read_text()


def test_parse_small_repo_with_few_dependents() -> None:
    page = parse_dependents_page(_load("sdsc-ordes_gimie_repository.html"))

    assert page.selected_kind == "REPOSITORY"
    assert page.repository_count == 6
    assert page.package_count == 0
    # GitHub shows 4 rows even though the count is 6 (forks/duplicates not listed).
    assert len(page.items) == 4
    assert page.next_cursor_url is None  # single-page result

    first = page.items[0]
    assert first.full_name == "Imaging-Plaza/git-metadata-extractor"
    assert first.owner == "Imaging-Plaza"
    assert first.repo == "git-metadata-extractor"
    assert first.stars >= 0
    assert first.forks >= 0


def test_parse_huge_repo_extracts_paginated_cursor() -> None:
    page = parse_dependents_page(_load("psf_requests_repository.html"))

    assert page.selected_kind == "REPOSITORY"
    assert page.repository_count == 4_065_575
    assert page.package_count == 138_519
    assert len(page.items) == 30  # GitHub paginates by 30 rows per page
    assert page.next_cursor_url is not None
    assert "dependents_after=" in page.next_cursor_url


def test_parse_empty_dependents_page() -> None:
    page = parse_dependents_page(_load("python-poetry_poetry_package.html"))

    assert page.selected_kind == "PACKAGE"
    assert page.repository_count == 0
    assert page.package_count == 0
    assert page.items == []
    assert page.next_cursor_url is None


def test_parse_handles_empty_html_safely() -> None:
    page = parse_dependents_page("")
    assert page.repository_count == 0
    assert page.package_count == 0
    assert page.selected_kind is None
    assert page.items == []
    assert page.next_cursor_url is None


def test_iterate_dependents_walks_pages_via_fetcher_until_no_next() -> None:
    """Iterator should stop when a page returns no Next link."""

    gimie_html = _load("sdsc-ordes_gimie_repository.html")

    items = list(
        iterate_dependents(
            "sdsc-ordes/gimie",
            kind="REPOSITORY",
            fetcher=[gimie_html],
            max_pages=10,
            max_items=100,
        ),
    )

    assert len(items) == 4
    full_names = [item.full_name for item, _ in items]
    assert full_names[0] == "Imaging-Plaza/git-metadata-extractor"


def test_iterate_dependents_caps_at_max_items() -> None:
    """Iterator must stop yielding after `max_items`, even if pages have more."""

    requests_html = _load("psf_requests_repository.html")
    items = list(
        iterate_dependents(
            "psf/requests",
            kind="REPOSITORY",
            fetcher=[requests_html, requests_html, requests_html],
            max_pages=10,
            max_items=5,
        ),
    )
    assert len(items) == 5


def test_iterate_dependents_caps_at_max_pages() -> None:
    """Iterator must stop processing after `max_pages` regardless of items."""

    requests_html = _load("psf_requests_repository.html")
    items = list(
        iterate_dependents(
            "psf/requests",
            kind="REPOSITORY",
            fetcher=[requests_html, requests_html, requests_html],
            max_pages=2,
            max_items=200,
        ),
    )
    # Each page has 30 rows → 2 pages = 60 rows, capped before page 3.
    assert len(items) == 60


def test_iterate_dependents_stops_on_empty_html() -> None:
    """Empty HTML signals fetch failure — iterator should stop, not loop."""

    items = list(
        iterate_dependents(
            "any/repo",
            fetcher=["", ""],
            max_pages=5,
            max_items=100,
        ),
    )
    assert items == []


def test_build_dependents_url_default_kind() -> None:
    url = build_dependents_url("sdsc-ordes/gimie")
    assert url == (
        "https://github.com/sdsc-ordes/gimie/network/dependents"
        "?dependent_type=REPOSITORY"
    )


def test_build_dependents_url_with_cursor() -> None:
    url = build_dependents_url(
        "psf/requests",
        kind="REPOSITORY",
        cursor="abc123",
    )
    assert "dependent_type=REPOSITORY" in url
    assert "dependents_after=abc123" in url


def test_build_dependents_url_rejects_invalid_full_name() -> None:
    with pytest.raises(ValueError, match="owner/repo"):
        build_dependents_url("not-a-slug")
