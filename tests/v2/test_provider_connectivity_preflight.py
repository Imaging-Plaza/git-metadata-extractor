from __future__ import annotations

from typing import Any

import pytest

import scripts.v2.check_provider_connectivity as connectivity

REQUIRED_ENV_VARS = ("GITHUB_TOKEN", "INFOSCIENCE_TOKEN")
LOGFIRE_ENV_VAR = "LOGFIRE_TOKEN"
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


def test_get_missing_required_env_vars_reports_logfire_when_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "present-value")
    monkeypatch.setenv("INFOSCIENCE_TOKEN", "present-value")
    monkeypatch.delenv(LOGFIRE_ENV_VAR, raising=False)
    monkeypatch.setattr(connectivity, "_load_logfire_token_from_credentials", lambda: None)

    missing = connectivity.get_missing_required_env_vars(["logfire"])

    assert missing == [LOGFIRE_ENV_VAR]


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


def test_connectivity_main_fails_fast_when_logfire_token_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "present-value")
    monkeypatch.setenv("INFOSCIENCE_TOKEN", "present-value")
    monkeypatch.setenv(SELENIUM_ENV_VAR, "http://selenium:4444")
    monkeypatch.delenv(LOGFIRE_ENV_VAR, raising=False)
    monkeypatch.setattr(connectivity, "_load_logfire_token_from_credentials", lambda: None)

    exit_code = connectivity.main(["--providers", "logfire"])
    captured = capsys.readouterr()

    assert exit_code == MISSING_ENV_EXIT_CODE
    assert "Missing required environment variable(s):" in captured.err
    assert LOGFIRE_ENV_VAR in captured.err


def test_logfire_connectivity_calls_info_endpoint_with_env_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Response:
        status_code = 200

        @staticmethod
        def json() -> dict[str, Any]:
            return {"project_name": "git-metadata-extractor"}

    captured_call: dict[str, Any] = {}

    def _fake_get(url: str, *, headers: dict[str, str], timeout: int):
        captured_call["url"] = url
        captured_call["headers"] = headers
        captured_call["timeout"] = timeout
        return _Response()

    monkeypatch.setenv(LOGFIRE_ENV_VAR, "present-value")
    monkeypatch.setenv("LOGFIRE_BASE_URL", "https://logfire-eu.pydantic.dev")
    monkeypatch.setattr(connectivity, "_load_logfire_token_from_credentials", lambda: None)
    monkeypatch.setattr(connectivity.requests, "get", _fake_get)

    failures = connectivity.run_provider_connectivity_checks(
        ["logfire"],
        timeout_seconds=2,
    )

    assert failures == []
    assert captured_call == {
        "url": "https://logfire-eu.pydantic.dev/v1/info",
        "headers": {
            "Authorization": "present-value",
            "User-Agent": "git-metadata-extractor-preflight",
        },
        "timeout": 2,
    }


def test_logfire_connectivity_accepts_credentials_file_token_without_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials_token = "credentials-token"  # noqa: S105

    class _Response:
        status_code = 200

        @staticmethod
        def json() -> dict[str, Any]:
            return {"project_name": "git-metadata-extractor"}

    captured_auth_header: str | None = None

    def _fake_get(url: str, *, headers: dict[str, str], timeout: int):  # noqa: ARG001
        nonlocal captured_auth_header
        captured_auth_header = headers.get("Authorization")
        return _Response()

    monkeypatch.delenv(LOGFIRE_ENV_VAR, raising=False)
    monkeypatch.setattr(
        connectivity,
        "_load_logfire_token_from_credentials",
        lambda: credentials_token,
    )
    monkeypatch.setattr(
        connectivity,
        "_load_logfire_api_url_from_credentials",
        lambda: "https://logfire-eu.pydantic.dev",
    )
    monkeypatch.setattr(connectivity.requests, "get", _fake_get)

    failures = connectivity.run_provider_connectivity_checks(["logfire"])

    assert failures == []
    assert captured_auth_header == credentials_token


def test_logfire_connectivity_reports_unreachable_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raising_get(url: str, *, headers: dict[str, str], timeout: int):  # noqa: ARG001
        message = "network down"
        raise connectivity.requests.RequestException(message)

    monkeypatch.setenv(LOGFIRE_ENV_VAR, "present-value")
    monkeypatch.setenv("LOGFIRE_BASE_URL", "https://logfire-eu.pydantic.dev")
    monkeypatch.setattr(connectivity.requests, "get", _raising_get)

    failures = connectivity.run_provider_connectivity_checks(["logfire"])

    assert len(failures) == 1
    assert failures[0].startswith(f"logfire: {connectivity.LOGFIRE_UNREACHABLE_ERROR}")


def test_logfire_connectivity_warns_when_env_and_credentials_tokens_differ(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class _Response:
        status_code = 200

        @staticmethod
        def json() -> dict[str, Any]:
            return {"project_name": "git-metadata-extractor"}

    def _fake_get(url: str, *, headers: dict[str, str], timeout: int):  # noqa: ARG001
        return _Response()

    monkeypatch.setenv(LOGFIRE_ENV_VAR, "env-token")
    monkeypatch.setattr(
        connectivity,
        "_load_logfire_token_from_credentials",
        lambda: "credentials-token",
    )
    monkeypatch.setattr(
        connectivity,
        "_load_logfire_api_url_from_credentials",
        lambda: "https://logfire-eu.pydantic.dev",
    )
    monkeypatch.setattr(connectivity.requests, "get", _fake_get)

    failures = connectivity.run_provider_connectivity_checks(["logfire"])
    captured = capsys.readouterr()

    assert failures == []
    assert connectivity.LOGFIRE_TOKEN_SOURCE_WARNING in captured.out


def test_logfire_connectivity_fails_when_base_url_cannot_be_resolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(LOGFIRE_ENV_VAR, "present-value")
    monkeypatch.delenv("LOGFIRE_BASE_URL", raising=False)
    monkeypatch.setattr(connectivity, "_load_logfire_token_from_credentials", lambda: None)
    monkeypatch.setattr(connectivity, "_load_logfire_api_url_from_credentials", lambda: None)
    monkeypatch.setattr(connectivity, "_infer_logfire_base_url_from_token", lambda _token: None)

    failures = connectivity.run_provider_connectivity_checks(["logfire"])

    assert failures == [f"logfire: {connectivity.LOGFIRE_BASE_URL_UNRESOLVED_ERROR}"]


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
