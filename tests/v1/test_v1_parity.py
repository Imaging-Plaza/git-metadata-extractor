# ruff: noqa: INP001, SLF001
from __future__ import annotations

import asyncio

import src.api as api_module
import src.v1.cache.cache_manager as cache_manager_module
import src.v2.api as v2_api_module

app = api_module.app

def _configure_cache(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CACHE_DB_PATH", str(tmp_path / "v1_parity_cache.db"))
    cache_manager_module._cache_manager = None


def test_v1_routes_remain_registered() -> None:
    route_paths = {
        path
        for route in app.routes
        for path in [getattr(route, "path", None)]
        if isinstance(path, str)
    }
    required_v1_routes = {
        "/",
        "/v1/org/llm/json/{full_path:path}",
        "/v1/user/llm/json/{full_path:path}",
        "/v1/repository/gimie/json-ld/{full_path:path}",
        "/v1/repository/llm/json-ld/{full_path:path}",
        "/v1/repository/llm/json/{full_path:path}",
        "/v1/cache/stats",
        "/v1/cache/entries",
        "/v1/cache/cleanup",
        "/v1/cache/clear",
        "/v1/cache/enable",
        "/v1/cache/disable",
        "/v1/cache/invalidate/{api_type}",
    }

    assert required_v1_routes <= route_paths


def test_v1_root_response_shape_is_unchanged() -> None:
    payload = api_module.index()
    assert isinstance(payload, dict)
    assert list(payload) == ["title"]
    assert "Git Metadata Extractor v2.0.1" in payload["title"]


def test_v1_cache_management_endpoints_respond(tmp_path, monkeypatch) -> None:
    _configure_cache(tmp_path, monkeypatch)

    stats_payload = asyncio.run(api_module.get_cache_stats())
    entries_payload = asyncio.run(
        api_module.list_cache_entries(
            api_type=None,
            limit=100,
            offset=0,
            include_expired=False,
        ),
    )
    cleanup_payload = asyncio.run(api_module.cleanup_cache())
    clear_payload = asyncio.run(api_module.clear_all_cache())

    assert "config" in stats_payload
    assert "entries" in entries_payload
    assert "message" in cleanup_payload
    assert "message" in clear_payload


def test_v2_routes_do_not_break_v1_cache_access(tmp_path, monkeypatch) -> None:
    _configure_cache(tmp_path, monkeypatch)

    v2_health = asyncio.run(v2_api_module.health())
    v1_stats = asyncio.run(api_module.get_cache_stats())

    assert v2_health.status in {"healthy", "degraded", "unhealthy"}
    assert "config" in v1_stats
