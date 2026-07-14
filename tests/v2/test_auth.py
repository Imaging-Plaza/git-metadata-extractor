from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from git_metadata_extractor.agents import ProviderSet
from git_metadata_extractor.api import v2_router
from git_metadata_extractor.providers.mock_github import MockGitHubProvider
from git_metadata_extractor.providers.mock_infoscience import MockInfoscienceProvider
from git_metadata_extractor.providers.mock_orcid import MockORCIDProvider
from git_metadata_extractor.providers.mock_ror import MockRORProvider

HTTP_OK = 200
HTTP_UNAUTHORIZED = 401
HTTP_SERVICE_UNAVAILABLE = 503

VALID_TOKEN = "s3cret-test-token"  # noqa: S105 (test fixture, not a real secret)


def _build_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(v2_router)
    app.state.v2_provider_set = ProviderSet(
        github=MockGitHubProvider(),
        orcid=MockORCIDProvider(),
        infoscience=MockInfoscienceProvider(),
        ror=MockRORProvider(),
    )
    return app


def _request(
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, str], Any]:
    async def _run() -> tuple[int, dict[str, str], Any]:
        app = _build_test_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            response = await client.request(
                method,
                path,
                headers=headers,
                json=json_body,
            )
        body: Any
        try:
            body = response.json()
        except ValueError:
            body = response.text
        return response.status_code, dict(response.headers), body

    return asyncio.run(_run())


@pytest.fixture
def configured_token(monkeypatch: pytest.MonkeyPatch) -> str:
    """Pin a known token for the test."""
    monkeypatch.setenv("API_TOKEN", VALID_TOKEN)
    return VALID_TOKEN


def test_extract_returns_401_when_token_missing(configured_token: str) -> None:
    del configured_token
    status_code, headers, _body = _request(
        "GET",
        "/v2/extract/github.com/octocat/Hello-World",
    )

    assert status_code == HTTP_UNAUTHORIZED
    assert headers.get("www-authenticate") == "Bearer"


def test_extract_returns_401_when_token_wrong(configured_token: str) -> None:
    del configured_token
    status_code, headers, _body = _request(
        "GET",
        "/v2/extract/github.com/octocat/Hello-World",
        headers={"Authorization": "Bearer wrong-token"},
    )

    assert status_code == HTTP_UNAUTHORIZED
    assert headers.get("www-authenticate") == "Bearer"


def test_extract_succeeds_with_correct_token(configured_token: str) -> None:
    status_code, _headers, payload = _request(
        "GET",
        "/v2/extract/github.com/octocat/Hello-World",
        headers={"Authorization": f"Bearer {configured_token}"},
    )

    assert status_code == HTTP_OK
    assert payload["detected_type"] == "repository"


def test_health_stays_open_without_token(configured_token: str) -> None:
    del configured_token
    status_code, _headers, payload = _request("GET", "/v2/health")

    assert status_code == HTTP_OK
    assert "status" in payload


def test_returns_503_when_token_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Auth fails closed: missing API_TOKEN never silently goes open."""
    monkeypatch.delenv("API_TOKEN", raising=False)

    status_code, _headers, _body = _request(
        "GET",
        "/v2/extract/github.com/octocat/Hello-World",
        headers={"Authorization": "Bearer anything"},
    )

    assert status_code == HTTP_SERVICE_UNAVAILABLE
