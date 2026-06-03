"""`fetch_by_slug` recovers numeric-slug communities via search (#6 v2).

Zenodo's direct `GET /api/communities/<slug>` returns an empty search envelope
(HTTP 200, zero hits) for numeric slugs like `101060684` (the BIORECER project
community), so the direct lookup yields no community object. `fetch_by_slug`
must fall back to `?q=<slug>` and match the slug exactly.
"""

from __future__ import annotations

from typing import Any

import src.index.zenodo_communities.ingest.zenodo as zmod


class _Resp:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload


_EMPTY_ENVELOPE = {"hits": {"hits": [], "total": 0}}


def _community_payload(slug: str) -> dict:
    return {"slug": slug, "metadata": {"title": "BIORECER"}, "links": {}}


def test_numeric_slug_recovered_via_search(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_get(url: str, params: dict | None = None, timeout: Any = None) -> _Resp:
        calls.append({"url": url, "params": params})
        if params and "q" in params:  # the ?q= search fallback
            return _Resp(200, {"hits": {"hits": [_community_payload("101060684")]}})
        return _Resp(200, _EMPTY_ENVELOPE)  # direct lookup: empty envelope

    monkeypatch.setattr(zmod.requests, "get", fake_get)

    record = zmod.fetch_by_slug("101060684")
    assert record is not None
    assert record["source_slug"] == "101060684"
    assert record["title"] == "BIORECER"
    # direct lookup tried first, then the search fallback
    assert any("q" in (c["params"] or {}) for c in calls)


def test_direct_hit_short_circuits(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_get(url: str, params: dict | None = None, timeout: Any = None) -> _Resp:
        calls.append({"url": url, "params": params})
        return _Resp(200, _community_payload("epfl"))  # direct returns a real community

    monkeypatch.setattr(zmod.requests, "get", fake_get)

    record = zmod.fetch_by_slug("epfl")
    assert record is not None
    assert record["source_slug"] == "epfl"
    # no ?q= fallback when the direct lookup already succeeded
    assert all("q" not in (c["params"] or {}) for c in calls)


def test_search_fallback_ignores_non_matching_slug(monkeypatch) -> None:
    def fake_get(url: str, params: dict | None = None, timeout: Any = None) -> _Resp:
        if params and "q" in params:
            # search returns a different community — must NOT be accepted
            return _Resp(200, {"hits": {"hits": [_community_payload("something-else")]}})
        return _Resp(200, _EMPTY_ENVELOPE)

    monkeypatch.setattr(zmod.requests, "get", fake_get)
    assert zmod.fetch_by_slug("101060684") is None
