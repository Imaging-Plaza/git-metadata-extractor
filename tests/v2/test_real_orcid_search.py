from __future__ import annotations

from pathlib import Path
from typing import Any

from git_metadata_extractor.providers.cache import ProviderCache
from git_metadata_extractor.providers.orcid_provider import (
    EXPANDED_SEARCH_EDISMAX,
    RealORCIDProvider,
)

STATUS_ERROR_THRESHOLD = 400


class _FakeResponse:
    def __init__(self, *, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= STATUS_ERROR_THRESHOLD:
            message = f"HTTP {self.status_code}"
            raise RuntimeError(message)

    def json(self) -> dict[str, Any]:
        return self._payload


class _RecordingSession:
    """Fake `requests.Session` that records every GET and returns a canned payload."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.calls: list[dict[str, Any]] = []

    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> _FakeResponse:
        self.calls.append(
            {"url": url, "params": params, "timeout": timeout, "headers": headers},
        )
        return _FakeResponse(status_code=200, payload=self._payload)


_EXAMPLE_PAYLOAD: dict[str, Any] = {
    "expanded-result": [
        {
            "orcid-id": "0000-0001-2345-6789",
            "given-names": "Noemie",
            "family-names": "Mazare",
            "credit-name": "Noemie Mazare",
            "other-name": ["N. Mazare"],
            "email": ["noemie@example.org"],
            "institution-name": ["EPFL", "University of Lausanne"],
        },
        {
            # Garbage-typed entries should be tolerated.
            "orcid-id": None,
            "given-names": 42,
            "family-names": "",
            "credit-name": "Other Person",
            "other-name": "not-a-list",
            "email": ["valid@example.org", 7, ""],
            "institution-name": None,
        },
    ],
    "num-found": 2,
}


def test_search_persons_builds_edismax_query_and_passes_pagination() -> None:
    session = _RecordingSession(_EXAMPLE_PAYLOAD)
    provider = RealORCIDProvider(session=session)

    hits = provider.search_persons("noemie mazare", rows=25, start=10)

    assert len(session.calls) == 1
    call = session.calls[0]
    assert call["url"].endswith("/v3.0/expanded-search/")
    assert call["params"] == {
        "q": f"{EXPANDED_SEARCH_EDISMAX}noemie mazare",
        "start": 10,
        "rows": 25,
    }
    assert call["headers"] == {"Accept": "application/json"}

    assert len(hits) == 2
    first = hits[0]
    assert first["orcid_id"] == "0000-0001-2345-6789"
    assert first["given_names"] == "Noemie"
    assert first["family_names"] == "Mazare"
    assert first["credit_name"] == "Noemie Mazare"
    assert first["other_names"] == ["N. Mazare"]
    assert first["institution_names"] == ["EPFL", "University of Lausanne"]
    assert first["emails"] == ["noemie@example.org"]

    second = hits[1]
    # None / wrong-typed scalars become None; non-list scalars become [].
    assert second["orcid_id"] is None
    assert second["given_names"] is None
    assert second["family_names"] is None
    assert second["credit_name"] == "Other Person"
    assert second["other_names"] == []
    assert second["institution_names"] == []
    # Empty / non-string entries are filtered out.
    assert second["emails"] == ["valid@example.org"]


def test_search_persons_strips_query_and_clamps_rows() -> None:
    session = _RecordingSession(_EXAMPLE_PAYLOAD)
    provider = RealORCIDProvider(session=session)

    provider.search_persons("  noemie  ", rows=10_000, start=-5)

    params = session.calls[0]["params"]
    assert params["q"] == f"{EXPANDED_SEARCH_EDISMAX}noemie"
    assert params["rows"] == 200  # capped at EXPANDED_SEARCH_MAX_ROWS
    assert params["start"] == 0  # negative offsets clamped to zero


def test_search_persons_empty_query_short_circuits_without_http() -> None:
    session = _RecordingSession(_EXAMPLE_PAYLOAD)
    provider = RealORCIDProvider(session=session)

    assert provider.search_persons("   ") == []
    assert session.calls == []


def test_search_persons_returns_empty_when_payload_lacks_expanded_result() -> None:
    session = _RecordingSession({"num-found": 0})
    provider = RealORCIDProvider(session=session)

    assert provider.search_persons("noemie") == []


def test_search_persons_uses_provider_cache_to_skip_duplicate_calls(
    tmp_path: Path,
) -> None:
    cache = ProviderCache(tmp_path / "cache.sqlite")
    session = _RecordingSession(_EXAMPLE_PAYLOAD)
    provider = RealORCIDProvider(session=session, cache=cache)

    first_hits = provider.search_persons("noemie", rows=5, start=0)
    second_hits = provider.search_persons("noemie", rows=5, start=0)

    assert first_hits == second_hits
    # Second call must be served from cache — no extra HTTP call.
    assert len(session.calls) == 1

    # Different (rows, start) is a distinct cache key and therefore re-hits HTTP.
    provider.search_persons("noemie", rows=5, start=5)
    assert len(session.calls) == 2
