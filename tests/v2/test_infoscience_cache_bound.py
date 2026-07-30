# tests/v2/test_infoscience_cache_bound.py
"""Bug 03: the infoscience in-memory search cache is never cleared during
serving, so an unbounded dict leaks for the whole process lifetime. It is now a
size-bounded FIFO cache.
"""
from __future__ import annotations

from git_metadata_extractor.providers.infoscience import _BoundedStrCache


def test_evicts_oldest_past_maxsize():
    cache = _BoundedStrCache(maxsize=3)
    for i in range(5):
        cache[f"k{i}"] = str(i)
    assert len(cache) == 3
    assert "k0" not in cache and "k1" not in cache  # oldest two evicted
    assert "k4" in cache and cache["k4"] == "4"


def test_reinsert_refreshes_recency():
    cache = _BoundedStrCache(maxsize=2)
    cache["a"] = "1"
    cache["b"] = "2"
    cache["a"] = "3"          # refresh "a" → now newest
    cache["c"] = "4"          # evicts oldest, which is now "b"
    assert "a" in cache and cache["a"] == "3"
    assert "b" not in cache
    assert "c" in cache


def test_behaves_like_a_dict_for_existing_access_pattern():
    cache = _BoundedStrCache(maxsize=8)
    cache["q"] = "result"
    assert "q" in cache          # `if cache_key in _search_cache`
    assert cache["q"] == "result"  # `return _search_cache[cache_key]`
    cache.clear()                 # clear_infoscience_cache()
    assert "q" not in cache and len(cache) == 0


def test_maxsize_floor_is_one():
    cache = _BoundedStrCache(maxsize=0)
    cache["a"] = "1"
    cache["b"] = "2"
    assert len(cache) == 1 and "b" in cache
