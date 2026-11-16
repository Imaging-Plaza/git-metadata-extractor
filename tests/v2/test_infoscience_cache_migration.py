"""Old provider-cache empties may represent swallowed upstream failures."""

import pytest

from git_metadata_extractor.providers.base import ProviderError
from git_metadata_extractor.providers.cache import ProviderCache
from git_metadata_extractor.providers.infoscience_provider import (
    RealInfoscienceProvider,
)


@pytest.mark.parametrize(("method", "search_arg", "bucket"), [
    ("search_person", "search_authors_func", "authors"),
    ("search_orgunit", "search_labs_func", "labs"),
    ("search_publications", "search_publications_func", "publications"),
])
def test_legacy_empty_cache_cannot_hide_current_failure_or_recovery(tmp_path, method, search_arg, bucket):
    cache = ProviderCache(tmp_path / "providers.sqlite")
    legacy_key = ProviderCache.make_key("infoscience", method, query="EPFL", max_results=10)
    cache.set(legacy_key, [])
    failing = True
    calls = []

    async def search(query, max_results):
        calls.append((query, max_results))
        if failing:
            message = "Infoscience HTTP 429; retry after 600s"
            raise ProviderError(message)
        return {bucket: []}

    provider = RealInfoscienceProvider(cache=cache, **{search_arg: search})
    lookup = getattr(provider, method)
    for _ in range(2):
        with pytest.raises(ProviderError, match="429"):
            lookup("EPFL")
    assert calls == [("EPFL", 10), ("EPFL", 10)]
    assert cache.get(legacy_key) == []  # Migration needs no destructive cache clear.

    failing = False
    assert lookup("EPFL") == []
    assert lookup("EPFL") == []  # A fresh successful empty search still caches.
    assert calls == [("EPFL", 10), ("EPFL", 10), ("EPFL", 10)]
