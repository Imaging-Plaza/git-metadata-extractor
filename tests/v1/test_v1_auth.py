# tests/v1/test_v1_auth.py
"""Audit findings v1-*-no-auth / cache-*-unauth-destructive: the v1 extraction
and cache-management routes were unauthenticated while v2 required a bearer
token (and the docs claimed v1 shared it). They now enforce verify_token.
"""
from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from src.api import app as main_app

UNAUTHORIZED = 401

V1_PROTECTED = [
    ("GET", "/v1/cache/stats"),
    ("GET", "/v1/cache/entries"),
    ("POST", "/v1/cache/cleanup"),
    ("POST", "/v1/cache/clear"),
    ("POST", "/v1/cache/enable"),
    ("POST", "/v1/cache/disable"),
    ("DELETE", "/v1/cache/invalidate/github"),
    ("GET", "/v1/org/llm/json/github.com/foo"),
    ("GET", "/v1/user/llm/json/github.com/foo"),
    ("GET", "/v1/repository/llm/json/github.com/foo/bar"),
]


def _status(method: str, path: str, headers: dict | None = None) -> int:
    async def _run() -> int:
        transport = ASGITransport(app=main_app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.request(method, path, headers=headers or {})
            return resp.status_code

    return asyncio.run(_run())


@pytest.mark.parametrize(("method", "path"), V1_PROTECTED)
def test_v1_route_rejects_missing_token(method, path, monkeypatch):
    monkeypatch.setenv("API_TOKEN", "secret")
    assert _status(method, path) == UNAUTHORIZED


@pytest.mark.parametrize(("method", "path"), V1_PROTECTED)
def test_v1_route_rejects_wrong_token(method, path, monkeypatch):
    monkeypatch.setenv("API_TOKEN", "secret")
    assert _status(method, path, {"Authorization": "Bearer nope"}) == UNAUTHORIZED


def test_v1_cache_stats_passes_auth_with_valid_token(monkeypatch):
    # The auth gate must let a valid token through (handler then runs — we only
    # assert auth no longer blocks it, not the handler's specific status).
    monkeypatch.setenv("API_TOKEN", "secret")
    code = _status("GET", "/v1/cache/stats", {"Authorization": "Bearer secret"})
    assert code not in (401, 503)
