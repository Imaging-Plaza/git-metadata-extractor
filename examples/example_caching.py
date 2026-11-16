"""Demonstrate provider-cache hits and scoped refresh without external calls."""

from __future__ import annotations

import argparse
from pathlib import Path
from uuid import uuid4

from git_metadata_extractor.providers.cache import (
    ProviderCache,
    reset_cache_refresh,
    set_cache_refresh,
)


def demonstrate(db_path: Path) -> list[int]:
    """Return the fetch counter after a miss, hit, refresh, and another hit."""
    cache = ProviderCache(db_path)
    key = cache.make_key("example", "synthetic_record", run=str(uuid4()))
    calls = 0

    def fetch() -> dict[str, int]:
        nonlocal calls
        calls += 1
        return {"fetch_number": calls}

    first = cache.get_or_set(key, fetch)
    cached = cache.get_or_set(key, fetch)
    token = set_cache_refresh(True)
    try:
        refreshed = cache.get_or_set(key, fetch)
    finally:
        reset_cache_refresh(token)
    reused = cache.get_or_set(key, fetch)
    return [value["fetch_number"] for value in (first, cached, refreshed, reused)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache-path",
        type=Path,
        default=Path("data/examples/cache.db"),
    )
    args = parser.parse_args()
    counts = demonstrate(args.cache_path)
    print(f"Fetch counters (miss, hit, refresh, hit): {counts}")
    print(f"Demonstration cache: {args.cache_path}; service caches were untouched.")


if __name__ == "__main__":
    main()
