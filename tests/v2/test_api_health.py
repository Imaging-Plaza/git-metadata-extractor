from __future__ import annotations

import asyncio
import time
from importlib.metadata import version as package_version
from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.v2.api import v2_router
from src.v2.models.contracts import V2HealthResponse

HTTP_OK = 200
PACKAGE_NAME = "git-metadata-extractor"
GITHUB_COMPONENT = "github_token"
HEALTH_ENDPOINT_MAX_MS = 100


def _build_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(v2_router)
    return app


def _get_json(path: str) -> tuple[int, Any, float]:
    async def _run() -> tuple[int, Any, float]:
        test_app = _build_test_app()
        transport = ASGITransport(app=test_app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            started = time.perf_counter()
            response = await client.get(path)
            elapsed_ms = (time.perf_counter() - started) * 1000
        return response.status_code, response.json(), elapsed_ms

    return asyncio.run(_run())


def test_health_returns_healthy_when_all_checks_pass(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    status_code, payload, _elapsed_ms = _get_json("/v2/health")

    assert status_code == HTTP_OK
    assert payload["status"] == "healthy"
    assert payload["components"]["config"] == "healthy"
    assert payload["components"]["graph_store"] == "healthy"
    assert payload["components"][GITHUB_COMPONENT] == "healthy"
    assert V2HealthResponse.model_validate(payload)


def test_health_degrades_when_github_token_is_missing(
    monkeypatch,
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    status_code, payload, _elapsed_ms = _get_json("/v2/health")

    assert status_code == HTTP_OK
    assert payload["status"] == "degraded"
    assert payload["components"][GITHUB_COMPONENT] == "degraded"


def test_health_includes_package_version(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    status_code, payload, _elapsed_ms = _get_json("/v2/health")

    assert status_code == HTTP_OK
    assert payload["version"] == package_version(PACKAGE_NAME)


def test_health_endpoint_responds_under_100ms(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    status_code, _payload, elapsed_ms = _get_json("/v2/health")

    assert status_code == HTTP_OK
    assert elapsed_ms < HEALTH_ENDPOINT_MAX_MS
