from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from git_metadata_extractor.agents import ProviderSet
from git_metadata_extractor.api import v2_router
from git_metadata_extractor.app import app as main_app
from git_metadata_extractor.auth import verify_token
from git_metadata_extractor.providers.mock_github import MockGitHubProvider
from git_metadata_extractor.providers.mock_infoscience import MockInfoscienceProvider
from git_metadata_extractor.providers.mock_orcid import MockORCIDProvider
from git_metadata_extractor.providers.mock_ror import MockRORProvider

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi.dependencies.models import Dependant

HTTP_OK = 200
HTTP_UNAUTHORIZED = 401
HTTP_SERVICE_UNAVAILABLE = 503

VALID_TOKEN = "s3cret-test-token"  # noqa: S105 (test fixture, not a real secret)

# What AGENTS.md documents as open, each with a marker its body must carry (a
# JSON key, or text of the HTML page): the welcome payload, the Swagger UI and
# the health check. Every other route must run `verify_token`, so adding one
# here is the decision to publish it unauthenticated — taken in review, not by
# leaving out a `Depends`.
OPEN_ROUTES = {
    ("GET", "/"): "title",
    ("GET", "/docs"): "swagger-ui",
    ("GET", "/v2/health"): "status",
}


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
    app: FastAPI | None = None,
) -> tuple[int, dict[str, str], Any]:
    async def _run() -> tuple[int, dict[str, str], Any]:
        transport = ASGITransport(app=app if app is not None else _build_test_app())
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


def _runs(dependant: Dependant, call: Callable[..., Any]) -> bool:
    """Whether `call` is anywhere in this route's dependency tree."""
    return any(sub.call is call or _runs(sub, call) for sub in dependant.dependencies)


def test_every_route_outside_the_open_list_runs_verify_token() -> None:
    """Asserted over the served app's route table rather than per endpoint.

    So a new endpoint that leaves out `Depends(verify_token)` fails here
    without anyone remembering to write it an auth test. A route FastAPI
    cannot describe — a raw Starlette route, a mounted sub-app — cannot run
    the dependency at all, so it fails too unless it is one of FastAPI's own
    schema and docs pages. What `verify_token` answers is the other tests'
    job; this one proves every route outside the list asks it.
    """
    framework_pages = {
        main_app.openapi_url,
        main_app.docs_url,
        main_app.redoc_url,
        main_app.swagger_ui_oauth2_redirect_url,
    } - {None}
    checked: list[str] = []
    unprotected: list[str] = []
    for route in main_app.routes:
        if not isinstance(route, APIRoute):
            if getattr(route, "path", None) not in framework_pages:
                unprotected.append(getattr(route, "path", repr(route)))
            continue
        for method in sorted(route.methods):
            if (method, route.path) in OPEN_ROUTES:
                continue
            checked.append(f"{method} {route.path}")
            if not _runs(route.dependant, verify_token):
                unprotected.append(f"{method} {route.path}")

    assert checked, "no protected routes registered"
    assert not unprotected, f"routes served without verify_token: {unprotected}"


@pytest.mark.parametrize(
    ("route", "marker"),
    sorted(OPEN_ROUTES.items()),
    ids=[f"{method} {path}" for method, path in sorted(OPEN_ROUTES)],
)
def test_open_routes_answer_without_a_token(
    configured_token: str,
    monkeypatch: pytest.MonkeyPatch,
    route: tuple[str, str],
    marker: str,
) -> None:
    """The other half of the list: each entry is served, and served openly."""
    del configured_token
    # `/v2/health` probes GitHub's rate limit whenever a token is present.
    monkeypatch.delenv("GME_GITHUB_TOKEN", raising=False)
    method, path = route

    status_code, _headers, body = _request(method, path, app=main_app)

    assert status_code == HTTP_OK
    assert marker in body


def test_returns_503_when_token_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Auth fails closed: missing API_TOKEN never silently goes open."""
    monkeypatch.delenv("API_TOKEN", raising=False)

    status_code, _headers, _body = _request(
        "GET",
        "/v2/extract/github.com/octocat/Hello-World",
        headers={"Authorization": "Bearer anything"},
    )

    assert status_code == HTTP_SERVICE_UNAVAILABLE
