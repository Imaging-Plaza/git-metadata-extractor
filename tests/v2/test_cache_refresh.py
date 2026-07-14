"""Backfill cache-refresh toggle on ProviderCache.

When the per-request refresh flag is active, `get_or_set` must bypass the read
(recompute every time) and overwrite the stored value — so a single re-extract
picks up current logic without clearing the whole cache.
"""

from __future__ import annotations

from pathlib import Path

from git_metadata_extractor.providers.cache import (
    ProviderCache,
    cache_refresh_active,
    reset_cache_refresh,
    set_cache_refresh,
)


def _cache(tmp_path: Path) -> ProviderCache:
    return ProviderCache(tmp_path / "c.db")


def test_get_or_set_caches_by_default(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    calls = {"n": 0}

    def factory() -> str:
        calls["n"] += 1
        return f"v{calls['n']}"

    assert cache.get_or_set("k", factory) == "v1"
    assert cache.get_or_set("k", factory) == "v1"  # served from cache
    assert calls["n"] == 1


def test_refresh_bypasses_read_and_overwrites(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    calls = {"n": 0}

    def factory() -> str:
        calls["n"] += 1
        return f"v{calls['n']}"

    assert cache.get_or_set("k", factory) == "v1"  # populate

    token = set_cache_refresh(True)
    try:
        assert cache_refresh_active() is True
        assert cache.get_or_set("k", factory) == "v2"  # recomputed, not served
    finally:
        reset_cache_refresh(token)

    assert calls["n"] == 2
    assert cache_refresh_active() is False
    # the refreshed value was written back
    assert cache.get_or_set("k", factory) == "v2"
    assert calls["n"] == 2  # served from cache again, no recompute


def test_refresh_default_is_off() -> None:
    assert cache_refresh_active() is False


def test_extract_request_has_refresh_field() -> None:
    from git_metadata_extractor.api_models.contracts import V2ExtractRequest

    req = V2ExtractRequest(source_url="https://github.com/x/y", refresh=True)
    assert req.refresh is True
    assert V2ExtractRequest(source_url="https://github.com/x/y").refresh is False
