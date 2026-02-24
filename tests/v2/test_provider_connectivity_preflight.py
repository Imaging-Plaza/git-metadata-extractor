from __future__ import annotations

from typing import Any

import pytest

import scripts.v2.check_provider_connectivity as connectivity

REQUIRED_ENV_VARS = ("GITHUB_TOKEN", "INFOSCIENCE_TOKEN")
SELENIUM_ENV_VAR = "SELENIUM_REMOTE_URL"
MISSING_ENV_EXIT_CODE = 2


@pytest.mark.parametrize("required_env_var", REQUIRED_ENV_VARS)
def test_get_missing_required_env_vars_reports_missing_names(
    monkeypatch: pytest.MonkeyPatch,
    required_env_var: str,
) -> None:
    for key in REQUIRED_ENV_VARS:
        monkeypatch.setenv(key, "present-value")
    monkeypatch.setenv(SELENIUM_ENV_VAR, "http://selenium:4444")

    monkeypatch.delenv(required_env_var, raising=False)

    missing = connectivity.get_missing_required_env_vars(["github", "infoscience"])

    assert missing == [required_env_var]


def test_get_missing_required_env_vars_reports_selenium_when_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "present-value")
    monkeypatch.setenv("INFOSCIENCE_TOKEN", "present-value")
    monkeypatch.delenv(SELENIUM_ENV_VAR, raising=False)

    missing = connectivity.get_missing_required_env_vars(["selenium"])

    assert missing == [SELENIUM_ENV_VAR]


def test_connectivity_main_fails_fast_when_required_env_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for key in REQUIRED_ENV_VARS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(SELENIUM_ENV_VAR, "http://selenium:4444")

    exit_code = connectivity.main(["--providers", "github", "infoscience"])
    captured = capsys.readouterr()

    assert exit_code == MISSING_ENV_EXIT_CODE
    assert "Missing required environment variable(s):" in captured.err
    assert "GITHUB_TOKEN" in captured.err
    assert "INFOSCIENCE_TOKEN" in captured.err


def test_connectivity_main_fails_fast_when_selenium_url_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "present-value")
    monkeypatch.setenv("INFOSCIENCE_TOKEN", "present-value")
    monkeypatch.delenv(SELENIUM_ENV_VAR, raising=False)

    exit_code = connectivity.main(["--providers", "selenium"])
    captured = capsys.readouterr()

    assert exit_code == MISSING_ENV_EXIT_CODE
    assert "Missing required environment variable(s):" in captured.err
    assert SELENIUM_ENV_VAR in captured.err


def test_infoscience_connectivity_accepts_empty_search_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _EmptyInfoscienceProvider:
        def __init__(self, *, max_results: int = 5) -> None:
            self.max_results = max_results

        def search_person(self, _query: str) -> list[dict[str, Any]]:
            return []

        def search_orgunit(self, _query: str) -> list[dict[str, Any]]:
            return []

        def search_publications(self, _query: str) -> list[dict[str, Any]]:
            return []

    monkeypatch.setattr(
        connectivity,
        "RealInfoscienceProvider",
        _EmptyInfoscienceProvider,
    )

    failures = connectivity.run_provider_connectivity_checks(["infoscience"])
    assert failures == []
