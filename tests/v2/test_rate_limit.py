# tests/v2/test_rate_limit.py
"""Audit finding no-rate-limit DoS: optional per-client rate limiting on the
compute-heavy routes. The limiter runs as middleware (before auth), so a bad
token still gets counted — letting us exercise it without running the pipeline.
"""
from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from src.api import app as main_app
from src.v2.rate_limit import reset_rate_limit_state

_HDR = {"Authorization": "Bearer wrong-token"}  # consistent key; fails auth → 401
TOO_MANY = 429
UNAUTHORIZED = 401


@pytest.fixture(autouse=True)
def _fresh_limiter():
    reset_rate_limit_state()
    yield
    reset_rate_limit_state()


def _post(path: str, headers: dict) -> int:
    async def _run() -> int:
        transport = ASGITransport(app=main_app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.post(path, headers=headers, json={})
            return resp.status_code

    return asyncio.run(_run())


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("V2_RATE_LIMIT_PER_MINUTE", raising=False)
    monkeypatch.setenv("API_TOKEN", "secret")
    codes = [_post("/v2/extract", _HDR) for _ in range(5)]
    assert codes == [UNAUTHORIZED] * 5  # never rate-limited when unset


def test_limits_when_configured(monkeypatch):
    monkeypatch.setenv("V2_RATE_LIMIT_PER_MINUTE", "2")
    monkeypatch.setenv("API_TOKEN", "secret")
    codes = [_post("/v2/extract", _HDR) for _ in range(4)]
    assert codes[:2] == [UNAUTHORIZED, UNAUTHORIZED]  # under the limit → reach auth
    assert TOO_MANY in codes[2:]                       # over the limit → 429


def test_health_is_not_rate_limited(monkeypatch):
    monkeypatch.setenv("V2_RATE_LIMIT_PER_MINUTE", "1")
    monkeypatch.setenv("API_TOKEN", "secret")

    async def _hits() -> list[int]:
        transport = ASGITransport(app=main_app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            return [(await client.get("/v2/health")).status_code for _ in range(3)]

    assert all(code != TOO_MANY for code in asyncio.run(_hits()))
