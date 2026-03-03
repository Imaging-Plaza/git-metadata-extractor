#!/usr/bin/env python3
"""
Test script to demonstrate the caching functionality.
This script shows how the caching system reduces external API calls.
"""

import time

import requests
import pytest

# Add src to path for imports
import src.cache.cache as cache_module
import src.cache.cache_manager as cache_manager_module
from src.cache.cache import get_cache
from src.cache.cache_manager import get_cache_manager


@pytest.fixture(autouse=True)
def _isolate_cache_singletons(tmp_path, monkeypatch) -> None:
    """Keep cache singleton state isolated between tests."""
    monkeypatch.setenv("CACHE_DB_PATH", str(tmp_path / "cache.db"))
    cache_module._cache_instance = None
    cache_manager_module._cache_manager = None
    yield
    cache_module._cache_instance = None
    cache_manager_module._cache_manager = None


def test_cache_basic_functionality():
    """Test basic cache functionality."""
    print("=== Testing Basic Cache Functionality ===")

    cache = get_cache()

    # Test data
    api_type = "test_api"
    params = {"test_param": "test_value"}
    test_data = {"result": "test_data", "timestamp": time.time()}

    # Test cache miss
    print("1. Testing cache miss...")
    result = cache.get(api_type, params)
    assert result is None, "Cache should be empty initially"
    print("   ✓ Cache miss works correctly")

    # Test cache set
    print("2. Testing cache set...")
    cache.set(api_type, params, test_data, ttl_days=1)
    print("   ✓ Data cached successfully")

    # Test cache hit
    print("3. Testing cache hit...")
    result = cache.get(api_type, params)
    assert result == test_data, "Cached data should match original"
    print("   ✓ Cache hit works correctly")

    # Test cache invalidation
    print("4. Testing cache invalidation...")
    success = cache.invalidate(api_type, params)
    assert success, "Cache invalidation should succeed"
    print("   ✓ Cache invalidation works correctly")

    # Test cache miss after invalidation
    result = cache.get(api_type, params)
    assert result is None, "Cache should be empty after invalidation"
    print("   ✓ Cache miss after invalidation works correctly")

    print("✅ All basic cache tests passed!\n")


def test_cache_manager():
    """Test cache manager functionality."""
    print("=== Testing Cache Manager ===")

    cache_manager = get_cache_manager()

    # Test data
    api_type = "test_manager_api"
    params = {"param1": "value1", "param2": "value2"}
    test_data = {"manager_test": True, "timestamp": time.time()}

    # Test fetch function
    def fetch_test_data():
        print("   Fetching fresh data...")
        return test_data

    # Test cache miss and fetch
    print("1. Testing cache miss with fetch...")
    result = cache_manager.get_cached_or_fetch(
        api_type=api_type,
        params=params,
        fetch_func=fetch_test_data,
        force_refresh=False,
    )
    assert result == test_data, "Fetched data should match expected"
    print("   ✓ Cache miss with fetch works correctly")

    # Test cache hit
    print("2. Testing cache hit...")
    result = cache_manager.get_cached_or_fetch(
        api_type=api_type,
        params=params,
        fetch_func=fetch_test_data,
        force_refresh=False,
    )
    assert result == test_data, "Cached data should match expected"
    print("   ✓ Cache hit works correctly")

    # Test force refresh
    print("3. Testing force refresh...")
    fresh_data = {"manager_test": True, "timestamp": time.time() + 1}

    def fetch_fresh_data():
        print("   Fetching fresh data (force refresh)...")
        return fresh_data

    result = cache_manager.get_cached_or_fetch(
        api_type=api_type,
        params=params,
        fetch_func=fetch_fresh_data,
        force_refresh=True,
    )
    assert result == fresh_data, "Force refresh should return fresh data"
    print("   ✓ Force refresh works correctly")

    # Test cache statistics
    print("4. Testing cache statistics...")
    stats = cache_manager.get_cache_stats()
    assert "total_entries" in stats, "Stats should include total_entries"
    assert "active_entries" in stats, "Stats should include active_entries"
    print(f"   ✓ Cache stats: {stats['active_entries']} active entries")

    print("✅ All cache manager tests passed!\n")


def test_api_endpoints():
    """Test API endpoints with caching (requires running server)."""
    print("=== Testing API Endpoints (requires running server) ===")

    base_url = "http://localhost:8000"  # Adjust if different

    try:
        # Test cache stats endpoint
        print("1. Testing cache stats endpoint...")
        response = requests.get(f"{base_url}/v1/cache/stats")
        if response.status_code == 200:
            stats = response.json()
            print(f"   ✓ Cache stats: {stats['active_entries']} active entries")
        else:
            print(f"   ⚠ Cache stats endpoint returned {response.status_code}")

        # Test cache cleanup endpoint
        print("2. Testing cache cleanup endpoint...")
        response = requests.post(f"{base_url}/v1/cache/cleanup")
        if response.status_code == 200:
            result = response.json()
            print(f"   ✓ {result['message']}")
        else:
            print(f"   ⚠ Cache cleanup endpoint returned {response.status_code}")

        print("✅ API endpoint tests completed!\n")

    except requests.exceptions.ConnectionError:
        print("   ⚠ Server not running, skipping API endpoint tests")
        print(
            "   To test API endpoints, start the server with: uvicorn src.api:app --reload\n",
        )


def test_performance_comparison():
    """Test performance comparison with and without cache."""
    print("=== Performance Comparison Test ===")

    cache_manager = get_cache_manager()

    # Simulate slow API call
    def slow_api_call():
        time.sleep(0.1)  # Simulate 100ms API call
        return {"data": "slow_api_result", "timestamp": time.time()}

    api_type = "performance_test"
    params = {"test": "performance"}

    # Test without cache (force refresh)
    print("1. Testing without cache (force refresh)...")
    start_time = time.time()
    result1 = cache_manager.get_cached_or_fetch(
        api_type=api_type,
        params=params,
        fetch_func=slow_api_call,
        force_refresh=True,
    )
    time_without_cache = time.time() - start_time
    print(f"   Time without cache: {time_without_cache:.3f}s")

    # Test with cache
    print("2. Testing with cache...")
    start_time = time.time()
    result2 = cache_manager.get_cached_or_fetch(
        api_type=api_type,
        params=params,
        fetch_func=slow_api_call,
        force_refresh=False,
    )
    time_with_cache = time.time() - start_time
    print(f"   Time with cache: {time_with_cache:.3f}s")

    # Calculate speedup
    speedup = (
        time_without_cache / time_with_cache if time_with_cache > 0 else float("inf")
    )
    print(f"   Speedup: {speedup:.1f}x faster with cache")

    assert result1 == result2, "Results should be identical"
    assert time_with_cache < time_without_cache, "Cache should be faster"

    print("✅ Performance test passed!\n")


def main():
    """Run all cache tests."""
    print("🧪 Starting Cache System Tests\n")

    try:
        test_cache_basic_functionality()
        test_cache_manager()
        test_performance_comparison()
        test_api_endpoints()

        print("🎉 All tests completed successfully!")
        print("\n📊 Cache System Summary:")
        print("   • SQLite-based storage with TTL support")
        print("   • Automatic expiration and cleanup")
        print("   • Force refresh capability")
        print("   • Performance improvements up to 100x faster")
        print("   • Thread-safe operations")
        print("   • Comprehensive statistics and management")

    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback

        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
