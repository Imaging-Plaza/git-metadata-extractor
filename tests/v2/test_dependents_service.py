"""Service-level tests: caching round-trip + degradation paths.

The service layer is exercised by passing an explicit `fetcher` callable
to `list_dependents`, so we never hit Selenium in tests.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from open_pulse_sources.module.dependents.scraper import build_dependents_url
from open_pulse_sources.module.dependents.service import list_dependents
from git_metadata_extractor.providers.cache import ProviderCache

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "github" / "dependents"


def _load(name: str) -> str:
    return (FIXTURE_DIR / name).read_text()


def _build_fixture_fetcher(url_to_html: dict[str, str]):
    """Make a `(url) -> html` callable that pretends to fetch our fixtures."""

    def _fetch(url: str) -> str:
        return url_to_html.get(url, "")

    return _fetch


def test_list_dependents_returns_structured_result_for_small_repo() -> None:
    fetcher = _build_fixture_fetcher(
        {
            build_dependents_url("sdsc-ordes/gimie", kind="REPOSITORY"):
                _load("sdsc-ordes_gimie_repository.html"),
        },
    )

    result = list_dependents(
        "sdsc-ordes/gimie",
        kind="REPOSITORY",
        fetcher=fetcher,
    )

    assert result.full_name == "sdsc-ordes/gimie"
    assert result.kind == "REPOSITORY"
    assert result.total_count == 6
    assert result.fetched_count == 4
    assert result.truncated is False  # we got every available row
    assert result.available is True
    assert result.warnings == []
    assert result.items[0].full_name == "Imaging-Plaza/git-metadata-extractor"


def test_list_dependents_marks_truncated_when_capped() -> None:
    fetcher = _build_fixture_fetcher(
        {
            build_dependents_url("psf/requests", kind="REPOSITORY"):
                _load("psf_requests_repository.html"),
        },
    )

    result = list_dependents(
        "psf/requests",
        kind="REPOSITORY",
        max_pages=1,
        max_items=10,
        fetcher=fetcher,
    )

    assert result.fetched_count == 10
    assert result.total_count == 4_065_575
    assert result.truncated is True


def test_list_dependents_returns_empty_result_for_disabled_graph() -> None:
    fetcher = _build_fixture_fetcher(
        {
            build_dependents_url("python-poetry/poetry", kind="PACKAGE"):
                _load("python-poetry_poetry_package.html"),
        },
    )

    result = list_dependents(
        "python-poetry/poetry",
        kind="PACKAGE",
        fetcher=fetcher,
    )

    assert result.total_count == 0
    assert result.fetched_count == 0
    assert result.available is True
    assert any("zero dependents" in w.lower() for w in result.warnings)


def test_list_dependents_rejects_invalid_full_name() -> None:
    result = list_dependents("not-a-slug", fetcher=lambda _u: "")

    assert result.available is False
    assert result.fetched_count == 0
    assert any("owner/repo" in w for w in result.warnings)


def test_list_dependents_caches_aggregated_result(tmp_path: Path) -> None:
    cache = ProviderCache(tmp_path / "providers.db")

    gimie_url = build_dependents_url("sdsc-ordes/gimie", kind="REPOSITORY")
    fetcher_calls: list[str] = []

    def _fetcher(url: str) -> str:
        fetcher_calls.append(url)
        return _load("sdsc-ordes_gimie_repository.html") if url == gimie_url else ""

    # First call populates cache.
    r1 = list_dependents(
        "sdsc-ordes/gimie",
        kind="REPOSITORY",
        cache=cache,
        fetcher=_fetcher,
    )
    fetcher_call_count_after_first = len(fetcher_calls)
    assert r1.fetched_count == 4
    assert fetcher_call_count_after_first >= 1  # at least one page fetched

    # Second call with the same args + a fetcher that always returns empty —
    # if cache is honoured, we still get the original result.
    def _empty_fetcher(_url: str) -> str:
        fetcher_calls.append("UNEXPECTED-FETCH")
        return ""

    r2 = list_dependents(
        "sdsc-ordes/gimie",
        kind="REPOSITORY",
        cache=cache,
        fetcher=_empty_fetcher,
    )

    assert r2.fetched_count == r1.fetched_count
    assert r2.total_count == r1.total_count
    assert r2.items[0].full_name == r1.items[0].full_name
    # No additional fetch calls after the cache populated.
    assert "UNEXPECTED-FETCH" not in fetcher_calls


def test_list_dependents_different_max_items_uses_different_cache_key(
    tmp_path: Path,
) -> None:
    """Different `max_items` must produce a different cache key."""

    cache = ProviderCache(tmp_path / "providers.db")
    gimie_url = build_dependents_url("sdsc-ordes/gimie", kind="REPOSITORY")
    fetcher_calls: list[str] = []

    def _fetcher(url: str) -> str:
        fetcher_calls.append(url)
        return _load("sdsc-ordes_gimie_repository.html") if url == gimie_url else ""

    list_dependents(
        "sdsc-ordes/gimie",
        kind="REPOSITORY",
        max_items=2,
        cache=cache,
        fetcher=_fetcher,
    )
    calls_after_first = len(fetcher_calls)

    # max_items=3 — different cache key, must re-fetch.
    list_dependents(
        "sdsc-ordes/gimie",
        kind="REPOSITORY",
        max_items=3,
        cache=cache,
        fetcher=_fetcher,
    )
    assert len(fetcher_calls) > calls_after_first
