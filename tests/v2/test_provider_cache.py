from __future__ import annotations

import time
from pathlib import Path

import pytest

from src.v2.ingest.cache import ProviderCache


def test_cache_miss_returns_none(tmp_path: Path) -> None:
    cache = ProviderCache(tmp_path / "p.db")
    assert cache.get("missing") is None


def test_cache_get_or_set_calls_factory_once(tmp_path: Path) -> None:
    cache = ProviderCache(tmp_path / "p.db")
    calls: list[int] = []

    def factory() -> dict[str, str]:
        calls.append(1)
        return {"name": "EPFL"}

    key = ProviderCache.make_key("ror", "search_organizations", query="EPFL")
    first = cache.get_or_set(key, factory)
    second = cache.get_or_set(key, factory)
    assert first == {"name": "EPFL"}
    assert second == {"name": "EPFL"}
    assert calls == [1]


def test_cache_set_with_explicit_ttl_expires(tmp_path: Path) -> None:
    cache = ProviderCache(tmp_path / "p.db", default_ttl_seconds=60)
    cache.set("k", {"v": 1}, ttl_seconds=0.05)
    assert cache.get("k") == {"v": 1}
    time.sleep(0.1)
    assert cache.get("k") is None


def test_factory_exception_does_not_cache(tmp_path: Path) -> None:
    cache = ProviderCache(tmp_path / "p.db")
    boom = RuntimeError("boom")

    def factory() -> dict[str, str]:
        raise boom

    key = ProviderCache.make_key("github", "get_user", username="ghost")
    with pytest.raises(RuntimeError):
        cache.get_or_set(key, factory)
    assert cache.get(key) is None


def test_none_factory_result_is_not_cached(tmp_path: Path) -> None:
    cache = ProviderCache(tmp_path / "p.db")
    calls: list[int] = []

    def factory() -> None:
        calls.append(1)
        return None

    key = ProviderCache.make_key("orcid", "get_person_by_orcid", orcid="0000-0000-0000-0000")
    cache.get_or_set(key, factory)
    cache.get_or_set(key, factory)
    assert calls == [1, 1]


def test_make_key_is_deterministic_and_arg_sensitive() -> None:
    a = ProviderCache.make_key("github", "get_user", username="a")
    b = ProviderCache.make_key("github", "get_user", username="a")
    c = ProviderCache.make_key("github", "get_user", username="b")
    assert a == b
    assert a != c


def test_clear_returns_real_deleted_count(tmp_path: Path) -> None:
    # `DELETE FROM <table>` with no WHERE triggers SQLite's truncate
    # optimization, under which `cursor.rowcount` is 0 even when rows are
    # removed. `clear()` must still report the true count.
    cache = ProviderCache(tmp_path / "p.db")
    for i in range(7):
        cache.set(f"k{i}", {"v": i})

    assert cache.clear() == 7
    assert cache.clear() == 0
    assert cache.get("k0") is None
