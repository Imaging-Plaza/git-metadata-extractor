"""Tool factory tests — mirror `test_llm_query_dependencies_tool.py`'s shape."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from open_pulse_sources.module.dependents.scraper import build_dependents_url
from open_pulse_sources.module.dependents.tool import make_query_dependents_tool

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "github" / "dependents"


def test_make_query_dependents_tool_exposes_expected_metadata() -> None:
    tool = make_query_dependents_tool()

    assert tool.name == "query_dependents"
    assert tool.description
    # Description should mention the key concept ("dependents") and explain
    # the fail-soft behaviour so the LLM doesn't panic on degraded results.
    assert "dependent" in tool.description.lower()
    assert "available" in tool.description.lower()


def test_query_dependents_tool_returns_dict_with_expected_keys(
    monkeypatch: Any,
) -> None:
    """End-to-end: tool function delegates to service, which uses our fetcher."""

    fixture_html = (FIXTURE_DIR / "sdsc-ordes_gimie_repository.html").read_text()
    expected_url = build_dependents_url("sdsc-ordes/gimie", kind="REPOSITORY")

    # Monkey-patch fetch_dependents_html so the tool's call path doesn't hit
    # Selenium. The tool uses `list_dependents` internally, which calls
    # `iterate_dependents` → `fetch_dependents_html`.
    #
    # `service.py` imports `fetch_dependents_html` by name from `scraper`,
    # so the local binding `open_pulse_sources.module.dependents.service.fetch_dependents_html`
    # must be patched — patching the scraper attribute only affects the
    # scraper module's own binding and leaves the service's copy untouched.
    fake_fetcher = lambda url, **_kwargs: fixture_html if url == expected_url else ""  # noqa: E731
    monkeypatch.setattr(
        "open_pulse_sources.module.dependents.scraper.fetch_dependents_html", fake_fetcher,
    )
    monkeypatch.setattr(
        "open_pulse_sources.module.dependents.service.fetch_dependents_html", fake_fetcher,
    )
    # Some env paths short-circuit if SELENIUM_REMOTE_URL is missing — keep
    # the service from refusing the lookup.
    monkeypatch.setenv("SELENIUM_REMOTE_URL", "http://test-selenium:4444")

    tool = make_query_dependents_tool()
    # Pull out the underlying callable — pydantic-ai exposes it via `.function`.
    func = getattr(tool, "function", None) or tool._function  # type: ignore[attr-defined]
    payload: dict[str, Any] = func("sdsc-ordes/gimie")

    assert payload["full_name"] == "sdsc-ordes/gimie"
    assert payload["kind"] == "REPOSITORY"
    assert payload["fetched_count"] == 4
    assert payload["total_count"] == 6
    assert payload["available"] is True
    assert isinstance(payload["items"], list)
    assert payload["items"][0]["full_name"] == "Imaging-Plaza/git-metadata-extractor"


def test_query_dependents_tool_invalid_input_returns_unavailable_result() -> None:
    """The tool should never raise for malformed input — it returns a degraded result."""

    tool = make_query_dependents_tool()
    func = getattr(tool, "function", None) or tool._function  # type: ignore[attr-defined]
    payload: dict[str, Any] = func("not-a-slug")

    assert payload["available"] is False
    assert payload["fetched_count"] == 0
    assert payload["warnings"]
