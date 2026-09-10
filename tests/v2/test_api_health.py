from __future__ import annotations

import asyncio
import time
from importlib.metadata import version as package_version
from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from git_metadata_extractor.api import v2_router
from git_metadata_extractor.api_models.contracts import V2HealthResponse
from git_metadata_extractor.observation.github_rate_limit import GitHubRateLimitSummary

HTTP_OK = 200
PACKAGE_NAME = "git-metadata-extractor"
GITHUB_COMPONENT = "github_token"
HEALTH_ENDPOINT_MAX_MS = 100


def _healthy_rate_limit_summary() -> GitHubRateLimitSummary:
    """Minimal `GitHubRateLimitSummary` that reports `status="healthy"`.

    The real `probe_github_rate_limit()` makes a live call to
    `https://api.github.com/rate_limit`, which fails in CI (no live
    GitHub credentials) and reports the github_token component as
    `unhealthy`. Tests that need a healthy probe stub it.
    """
    return GitHubRateLimitSummary(
        status="healthy",
        total_remaining=5000,
        earliest_reset=None,
        tokens=[],
    )


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
    monkeypatch.setenv("GME_GITHUB_TOKEN", "test-token")
    monkeypatch.setattr(
        "git_metadata_extractor.api.system.probe_github_rate_limit",
        lambda: _healthy_rate_limit_summary(),
    )

    status_code, payload, _elapsed_ms = _get_json("/v2/health")

    assert status_code == HTTP_OK
    assert payload["status"] == "healthy"
    assert payload["components"]["config"] == "healthy"
    assert payload["components"][GITHUB_COMPONENT] == "healthy"
    assert V2HealthResponse.model_validate(payload)


def test_health_degrades_when_github_token_is_missing(
    monkeypatch,
) -> None:
    monkeypatch.delenv("GME_GITHUB_TOKEN", raising=False)

    status_code, payload, _elapsed_ms = _get_json("/v2/health")

    assert status_code == HTTP_OK
    assert payload["status"] == "degraded"
    assert payload["components"][GITHUB_COMPONENT] == "degraded"


def test_health_includes_package_version(monkeypatch) -> None:
    monkeypatch.setenv("GME_GITHUB_TOKEN", "test-token")

    status_code, payload, _elapsed_ms = _get_json("/v2/health")

    assert status_code == HTTP_OK
    assert payload["version"] == package_version(PACKAGE_NAME)


def test_health_endpoint_responds_under_100ms(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GME_GITHUB_TOKEN", "test-token")

    status_code, _payload, elapsed_ms = _get_json("/v2/health")

    assert status_code == HTTP_OK
    assert elapsed_ms < HEALTH_ENDPOINT_MAX_MS


# --------------------------------------------------------------------------
# the substrate store component
# --------------------------------------------------------------------------


def test_health_omits_the_substrate_store_when_the_writer_is_off(monkeypatch) -> None:
    """An opt-in feature that is switched off must not colour the status.

    Reporting the component unconditionally would report every default
    deployment as degraded for a store it was never asked to write to.
    """
    monkeypatch.delenv("V2_SUBSTRATE_ENABLED", raising=False)
    monkeypatch.setattr(
        "git_metadata_extractor.api.system.probe_github_rate_limit",
        _healthy_rate_limit_summary,
    )

    _status_code, body, _elapsed = _get_json("/v2/health")

    assert "substrate_store" not in body["components"]


def test_health_reports_the_substrate_store_as_degraded_when_unreachable(
    monkeypatch,
) -> None:
    """The writer fails open, so an unreachable store degrades rather than fails."""
    import httpx

    from git_metadata_extractor.store import oxigraph

    def handler(_request: httpx.Request) -> httpx.Response:
        message = "connection refused"
        raise httpx.ConnectError(message)

    real_from_config = oxigraph.store_from_config
    monkeypatch.setattr(
        oxigraph,
        "store_from_config",
        lambda url, **kwargs: real_from_config(
            url,
            **{**kwargs, "transport": httpx.MockTransport(handler)},
        ),
    )
    monkeypatch.setenv("V2_SUBSTRATE_ENABLED", "true")
    monkeypatch.setenv("V2_SUBSTRATE_STORE_URL", "http://oxigraph:7878")
    monkeypatch.setattr(
        "git_metadata_extractor.api.system.probe_github_rate_limit",
        _healthy_rate_limit_summary,
    )

    _status_code, body, _elapsed = _get_json("/v2/health")

    assert body["components"]["substrate_store"] == "degraded"
    assert body["status"] == "degraded"


def test_health_reports_the_substrate_store_as_degraded_when_unconfigured(
    monkeypatch,
) -> None:
    """Enabled with nowhere to write: still projected, just not stored."""
    monkeypatch.setenv("V2_SUBSTRATE_ENABLED", "true")
    monkeypatch.delenv("V2_SUBSTRATE_STORE_URL", raising=False)
    monkeypatch.setattr(
        "git_metadata_extractor.api.system.probe_github_rate_limit",
        _healthy_rate_limit_summary,
    )

    _status_code, body, _elapsed = _get_json("/v2/health")

    assert body["components"]["substrate_store"] == "degraded"


def test_health_reports_the_substrate_store_as_healthy_when_reachable(
    monkeypatch,
) -> None:
    import httpx

    from git_metadata_extractor.store import oxigraph

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text='{"head": {"vars": []}, "results": {"bindings": []}}',
        )

    real_from_config = oxigraph.store_from_config
    monkeypatch.setattr(
        oxigraph,
        "store_from_config",
        lambda url, **kwargs: real_from_config(
            url,
            **{**kwargs, "transport": httpx.MockTransport(handler)},
        ),
    )
    monkeypatch.setenv("V2_SUBSTRATE_ENABLED", "true")
    monkeypatch.setenv("V2_SUBSTRATE_STORE_URL", "http://oxigraph:7878")
    monkeypatch.setattr(
        "git_metadata_extractor.api.system.probe_github_rate_limit",
        _healthy_rate_limit_summary,
    )

    _status_code, body, _elapsed = _get_json("/v2/health")

    assert body["components"]["substrate_store"] == "healthy"


def test_health_reports_unhealthy_rather_than_500_on_an_unparseable_flag(
    monkeypatch,
) -> None:
    """A typo in an env var must not break the endpoint that exists to say so.

    `substrate_enabled()` raises `ValueError` on a value it cannot parse, the
    same as every other flag reader in `config.py`. Unwrapped, that surfaces as
    a 500 from `/v2/health` — the one response an operator cannot act on.
    """
    monkeypatch.setenv("V2_SUBSTRATE_ENABLED", "yes-please")
    monkeypatch.setattr(
        "git_metadata_extractor.api.system.probe_github_rate_limit",
        _healthy_rate_limit_summary,
    )

    status_code, body, _elapsed = _get_json("/v2/health")

    assert status_code == HTTP_OK
    assert body["components"]["substrate_store"] == "unhealthy"
    assert body["status"] == "unhealthy"
