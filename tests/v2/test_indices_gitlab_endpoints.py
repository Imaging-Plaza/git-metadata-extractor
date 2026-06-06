"""HTTP endpoints for the GitLab index family.

Covers route registration, a monkeypatched search round-trip (no real
Qdrant/RCP), auth enforcement, and the 503-when-no-provider-cache behaviour
of ingest on a bare app. Mirrors the harness in `test_api_manifest.py`.
"""

from __future__ import annotations

import asyncio
import importlib
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.v2.api import v2_router
from src.v2.indices.gitlab import GITLAB_INDEX_NAMES

if TYPE_CHECKING:
    import pytest

# Matches the token seeded by tests/v2/conftest.py's autouse env fixture.
TEST_API_TOKEN = "test-api-token"  # noqa: S105 — test fixture
_AUTH = {"Authorization": f"Bearer {TEST_API_TOKEN}"}

HTTP_OK = 200
HTTP_ACCEPTED = 202
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403
HTTP_SERVICE_UNAVAILABLE = 503

EXPECTED_GITLAB_STORES = 9


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(v2_router)
    return app


def _post(path: str, body: dict[str, Any], *, auth: bool = True) -> tuple[int, Any]:
    async def _run() -> tuple[int, Any]:
        transport = ASGITransport(app=_app())
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers=_AUTH if auth else {},
        ) as client:
            r = await client.post(path, json=body)
        ctype = r.headers.get("content-type", "")
        return r.status_code, (r.json() if ctype.startswith("application/json") else None)

    return asyncio.run(_run())


def _route_paths() -> set[str]:
    return {getattr(r, "path", "") for r in _app().routes}


def test_all_gitlab_ingest_and_search_routes_registered() -> None:
    paths = _route_paths()
    assert len(GITLAB_INDEX_NAMES) == EXPECTED_GITLAB_STORES
    for name in GITLAB_INDEX_NAMES:
        assert f"/v2/indices/{name}/ingest" in paths
        assert f"/v2/indices/{name}/search" in paths


def test_gitlab_search_returns_normalized_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_raw = {
        "id": "epfl-user-42",
        "vector_score": 0.9,
        "rerank_score": 0.8,
        "payload": {"username": "ada"},
        "entity": {"name": "Ada Lovelace"},
    }

    retrieval_mod = importlib.import_module(
        "src.index.gitlab_epfl_users.retrieval",
    )

    def _fake_search(query: str, **_kwargs: Any) -> list[dict[str, Any]]:  # noqa: ARG001
        return [fake_raw]

    # Patch the module attribute directly: run_gitlab_search imports the module
    # via importlib and reads `.search`. asyncio.run is used below, so the patch
    # must be in place before the request runs.
    monkeypatch.setattr(retrieval_mod, "search", _fake_search)

    status_code, body = _post(
        "/v2/indices/gitlab_epfl_users/search",
        {"query": "quantum researchers", "top_k": 5},
    )
    assert status_code == HTTP_OK
    assert body["index_name"] == "gitlab_epfl_users"
    assert body["query"] == "quantum researchers"
    assert len(body["hits"]) == 1
    assert body["hits"][0]["id"] == "epfl-user-42"


def test_gitlab_routes_expose_no_index_name_query_param() -> None:
    # The handlers are built by a factory; `index_name` must be bound via the
    # closure, NOT a function-signature default (which FastAPI would surface as
    # a `?index_name=` query param and let a caller redirect to another store).
    schema = _app().openapi()
    for name in GITLAB_INDEX_NAMES:
        for verb in ("ingest", "search"):
            op = schema["paths"][f"/v2/indices/{name}/{verb}"]["post"]
            param_names = {p["name"] for p in op.get("parameters", [])}
            assert "index_name" not in param_names, f"{name}/{verb} leaks index_name"


def test_gitlab_search_query_param_cannot_override_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Even if a caller appends ?index_name=<other>, the route's bound store wins.
    retrieval_mod = importlib.import_module("src.index.gitlab_epfl_users.retrieval")
    monkeypatch.setattr(
        retrieval_mod, "search",
        lambda _q, **_k: [{"id": "x", "payload": {}, "entity": {}}],
    )
    status_code, body = _post(
        "/v2/indices/gitlab_epfl_users/search?index_name=gitlab_ethz_users",
        {"query": "x", "top_k": 1},
    )
    assert status_code == HTTP_OK
    assert body["index_name"] == "gitlab_epfl_users"


def test_gitlab_search_requires_auth() -> None:
    status_code, _ = _post(
        "/v2/indices/gitlab_epfl_users/search",
        {"query": "x"},
        auth=False,
    )
    assert status_code in (HTTP_UNAUTHORIZED, HTTP_FORBIDDEN)


def test_gitlab_ingest_requires_auth() -> None:
    status_code, _ = _post(
        "/v2/indices/gitlab_epfl_users/ingest",
        {},
        auth=False,
    )
    assert status_code in (HTTP_UNAUTHORIZED, HTTP_FORBIDDEN)


def test_gitlab_ingest_503_without_provider_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # With the provider cache disabled the ingest job store cannot be resolved,
    # so the endpoint must return 503 (matches the github_users ingest path).
    monkeypatch.setenv("V2_PROVIDER_CACHE_ENABLED", "false")
    status_code, body = _post(
        "/v2/indices/gitlab_epfl_users/ingest",
        {"limit": 5},
    )
    assert status_code == HTTP_SERVICE_UNAVAILABLE
    assert "provider cache" in body["detail"]
