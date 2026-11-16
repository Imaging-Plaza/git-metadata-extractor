"""Upstream failures must remain failures, without flooding or poisoning caches."""

# ruff: noqa: SLF001, PLR2004

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import pytest

from git_metadata_extractor.agents import (
    OrganizationAgentV2,
    PersonAgentV2,
    ProviderSet,
)
from git_metadata_extractor.providers import infoscience
from git_metadata_extractor.providers.base import ProviderError
from git_metadata_extractor.providers.cache import ProviderCache
from git_metadata_extractor.providers.infoscience_provider import (
    RealInfoscienceProvider,
)
from git_metadata_extractor.providers.mock_github import MockGitHubProvider
from git_metadata_extractor.providers.mock_ror import MockRORProvider


@pytest.fixture
def clock(monkeypatch):
    state = SimpleNamespace(now=1000.0, waits=[])

    async def sleep(delay):
        state.waits.append(delay)
        state.now += delay

    monkeypatch.setattr(infoscience, "time", SimpleNamespace(
        monotonic=lambda: state.now, time=lambda: state.now,
    ), raising=False)
    monkeypatch.setattr(infoscience, "_sleep", sleep, raising=False)
    monkeypatch.setattr(infoscience, "_cooldown_until", 0.0, raising=False)
    monkeypatch.setattr(infoscience, "_request_gate", threading.Lock(), raising=False)
    infoscience.clear_infoscience_cache()
    return state


def transport(monkeypatch, handler):
    client_class = httpx.AsyncClient
    monkeypatch.setattr(infoscience.httpx, "AsyncClient", lambda **kwargs: client_class(
        transport=httpx.MockTransport(handler), **kwargs,
    ))


@pytest.mark.parametrize("retry_after", ["4", "Thu, 01 Jan 1970 00:16:44 GMT"])
def test_rate_limit_waits_for_server_before_recovering(monkeypatch, clock, retry_after):
    calls = []

    def handler(_request):
        calls.append(clock.now)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": retry_after})
        return httpx.Response(200, json={"recovered": True})

    transport(monkeypatch, handler)
    assert asyncio.run(infoscience._make_api_request("/search")) == {"recovered": True}
    assert calls == [1000.0, 1004.0]


def test_long_retry_after_fails_fast_and_suppresses_other_queries(monkeypatch, clock):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(429, headers={"Retry-After": "600"})

    transport(monkeypatch, handler)
    for search in (infoscience.search_labs, infoscience.search_authors, infoscience.search_publications):
        with pytest.raises(ProviderError):
            asyncio.run(search("EPFL"))
    clock.now += 60
    with pytest.raises(ProviderError):
        asyncio.run(infoscience.search_publications("EPFL"))
    assert len(calls) == 1  # No fallback query or extra callers during cooldown.
    assert clock.waits == []


@pytest.mark.usefixtures("clock")
@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_permanent_failure_is_not_retried_or_cached_as_empty(monkeypatch, tmp_path, status):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if len(calls) == 1:
            return httpx.Response(status)
        return httpx.Response(200, json={"_embedded": {"searchResult": {"page": {"totalElements": 0}}}})

    transport(monkeypatch, handler)
    provider = RealInfoscienceProvider(cache=ProviderCache(tmp_path / "cache.sqlite"))
    with pytest.raises(ProviderError):
        provider.search_publications("test")
    assert len(calls) == 1
    assert provider.search_publications("test") == []
    assert provider.search_publications("test") == []  # Successful empty hits still cache.
    assert len(calls) == 2


@pytest.mark.parametrize("failure", [429, 503, "timeout"])
def test_exhausted_transient_failure_recovers_without_cached_empty(monkeypatch, clock, tmp_path, failure):
    calls = []
    failing = True

    def handler(request):
        calls.append(clock.now)
        if failing:
            if failure == "timeout":
                message = "upstream timed out"
                raise httpx.ReadTimeout(message, request=request)
            return httpx.Response(failure)
        return httpx.Response(200, json={"_embedded": {"searchResult": {"page": {"totalElements": 0}}}})

    transport(monkeypatch, handler)
    provider = RealInfoscienceProvider(cache=ProviderCache(tmp_path / "cache.sqlite"))
    with pytest.raises(ProviderError):
        provider.search_publications("test")
    assert 1 < len(calls) <= 3
    calls_before = len(calls)
    with pytest.raises(ProviderError):
        provider.search_publications("another query")
    assert len(calls) == calls_before
    failing = False
    clock.now += 60
    assert provider.search_publications("test") == []
    assert len(calls) == calls_before + 1


def test_parallel_event_loops_share_single_inflight_request(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    counts = {"active": 0, "peak": 0}
    counts_lock = threading.Lock()
    monkeypatch.setattr(infoscience, "_cooldown_until", 0.0, raising=False)

    async def handler(_request):
        with counts_lock:
            counts["active"] += 1
            counts["peak"] = max(counts["peak"], counts["active"])
        entered.set()
        await asyncio.to_thread(release.wait, 2)
        with counts_lock:
            counts["active"] -= 1
        return httpx.Response(200, json={"ok": True})

    transport(monkeypatch, handler)
    with ThreadPoolExecutor(max_workers=3) as executor:
        first = executor.submit(asyncio.run, infoscience._make_api_request("/first"))
        assert entered.wait(2)
        rest = [executor.submit(asyncio.run, infoscience._make_api_request("/other")) for _ in range(2)]
        # Hold the first request while other event loops try to enter.
        threading.Event().wait(0.1)
        release.set()
        assert all(future.result() == {"ok": True} for future in [first, *rest])
    assert counts["peak"] == 1


@pytest.mark.parametrize("cancel_holder", [False, True])
def test_cancellation_does_not_leak_or_steal_gate(monkeypatch, cancel_holder):
    calls = []
    monkeypatch.setattr(infoscience, "_cooldown_until", 0.0, raising=False)

    async def run():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def handler(request):
            calls.append(request.url.path)
            if request.url.path.endswith("/first"):
                entered.set()
                await release.wait()
            return httpx.Response(200, json={"ok": True})

        transport(monkeypatch, handler)
        first = asyncio.create_task(infoscience._make_api_request("/first"))
        await entered.wait()
        waiter = asyncio.create_task(infoscience._make_api_request("/waiter"))
        await asyncio.sleep(0.02)
        cancelled = first if cancel_holder else waiter
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        release.set()
        await asyncio.wait_for(waiter if cancel_holder else first, timeout=2)
        assert await asyncio.wait_for(infoscience._make_api_request("/last"), timeout=2) == {"ok": True}

    asyncio.run(run())
    expected = ["/server/api/first", "/server/api/last"]
    if cancel_holder:
        expected.insert(1, "/server/api/waiter")
    assert calls == expected


@pytest.mark.parametrize("case", [
    ("person", False, "https://github.com/octocat"),
    ("organization", False, "https://github.com/github"),
    ("organization", True, "https://ror.org/02s376052"),
])
def test_optional_infoscience_failure_preserves_entity_and_can_recover(
    monkeypatch, clock, tmp_path, case,
):
    kind, with_ror, expected_id = case
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "600"})
        return httpx.Response(200, json={"_embedded": {"searchResult": {"page": {"totalElements": 0}}}})

    transport(monkeypatch, handler)
    providers = ProviderSet(
        github=MockGitHubProvider(),
        ror=MockRORProvider() if with_ror else None,
        infoscience=RealInfoscienceProvider(cache=ProviderCache(tmp_path / "cache.sqlite")),
    )
    agent = PersonAgentV2() if kind == "person" else OrganizationAgentV2()
    context = {"username": "octocat"} if kind == "person" else {"org_name": "github"}
    if with_ror:
        context["ror_id"] = "https://ror.org/02s376052"
    result = asyncio.run(agent.run(context, providers))
    assert result.data["id"] == expected_id
    assert any("Infoscience" in warning and "failed" in warning for warning in result.warnings)
    assert len(calls) == 1
    clock.now += 601
    recovered = asyncio.run(agent.run(context, providers))
    assert recovered.data["id"] == expected_id
    assert not any("Infoscience" in warning and "failed" in warning for warning in recovered.warnings)
    assert len(calls) > 1  # The failure did not become an empty cache hit.
    recovered_calls = len(calls)
    asyncio.run(agent.run(context, providers))
    assert len(calls) == recovered_calls  # Successful empty results still cache.


@pytest.mark.usefixtures("clock")
@pytest.mark.parametrize("core_status", [200, 404])
def test_entity_lookup_404_falls_back_to_core_items(monkeypatch, core_status):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if "/entities/" in request.url.path:
            return httpx.Response(404)
        return httpx.Response(core_status, json={"uuid": "known-item"})

    transport(monkeypatch, handler)
    result = asyncio.run(infoscience.get_entity_by_uuid("known-item", "person"))
    assert result == ({"uuid": "known-item"} if core_status == 200 else None)
    assert calls == [
        "/server/api/entities/person/known-item", "/server/api/core/items/known-item",
    ]


@pytest.mark.usefixtures("clock")
def test_entity_lookup_without_type_returns_none_for_missing_item(monkeypatch):
    transport(monkeypatch, lambda _request: httpx.Response(404))
    assert asyncio.run(infoscience.get_entity_by_uuid("missing")) is None


@pytest.mark.usefixtures("clock")
@pytest.mark.parametrize("entity_status", [404, 503])
def test_entity_lookup_propagates_availability_failure(monkeypatch, entity_status):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        status = entity_status if "/entities/" in request.url.path else 503
        return httpx.Response(status)

    transport(monkeypatch, handler)
    with pytest.raises(ProviderError):
        asyncio.run(infoscience.get_entity_by_uuid("known-item", "person"))
    if entity_status == 503:
        assert all("/entities/" in path for path in calls)
    else:
        assert calls[1:]
        assert all("/core/items/" in path for path in calls[1:])
