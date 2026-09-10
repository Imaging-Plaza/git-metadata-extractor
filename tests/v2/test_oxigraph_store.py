"""Tests for the Oxigraph client the substrate writer loads through.

Driven against `httpx.MockTransport` rather than a hand-rolled fake, because
what matters here *is* the protocol: which method, which path, which content
type, and — the load-bearing detail — that no `graph` query parameter is sent.
A fake with its own idea of the wire format would pass while the real store
returned 400, and there is no local Oxigraph in CI to catch that.

The one thing a mock cannot establish is that Oxigraph actually behaves this
way. That is verified separately against a real container; see the phase 3
notes in `REFACTOR_HANDOFF.md`.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from git_metadata_extractor.store.oxigraph import (
    OxigraphStore,
    StoreUnavailableError,
    StoreWriteError,
    store_from_config,
    summarise,
)

NQUADS = (
    '<https://github.com/a/b> <http://schema.org/name> "b" '
    "<urn:pulse:output:r1:github> .\n"
)

GITHUB_TRIPLES = 3
ROR_TRIPLES = 1


def _recorder(
    status: int = 204,
    body: str = "",
) -> tuple[list[httpx.Request], httpx.MockTransport]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, text=body)

    return seen, httpx.MockTransport(handler)


def _results(bindings: list[dict[str, Any]]) -> str:
    return json.dumps({"head": {"vars": []}, "results": {"bindings": bindings}})


def _count_binding(graph: str, count: int) -> dict[str, Any]:
    return {
        "g": {"type": "uri", "value": graph},
        "n": {"type": "literal", "value": str(count)},
    }


# --------------------------------------------------------------------------
# the write path
# --------------------------------------------------------------------------


def test_a_whole_run_is_one_post_to_store() -> None:
    """`POST /store` with no `graph` parameter dispatches quads by their graph.

    That is the reason the substrate can be loaded in a single request. Adding
    `?graph=` would silently retarget every quad into one named graph, which is
    the whole layer collapsed — and it would still return 204.
    """
    seen, transport = _recorder()
    store = OxigraphStore("http://oxigraph:7878", transport=transport)

    written = asyncio.run(store.load_nquads(NQUADS))

    assert len(seen) == 1
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/store"
    assert seen[0].url.params.get("graph") is None
    assert seen[0].headers["content-type"] == "application/n-quads"
    assert seen[0].content.decode() == NQUADS
    assert written == len(NQUADS.encode())


def test_an_empty_payload_writes_nothing() -> None:
    """A run that produced no substrate is not a store problem."""
    seen, transport = _recorder()
    store = OxigraphStore("http://oxigraph:7878", transport=transport)

    assert asyncio.run(store.load_nquads("")) == 0
    assert asyncio.run(store.load_nquads("   \n")) == 0
    assert seen == []


def test_a_trailing_slash_on_the_base_url_does_not_double_up() -> None:
    seen, transport = _recorder()
    store = OxigraphStore("http://oxigraph:7878/", transport=transport)

    asyncio.run(store.load_nquads(NQUADS))

    assert str(seen[0].url) == "http://oxigraph:7878/store"


def test_a_rejected_payload_raises_write_error_with_the_detail() -> None:
    """The parse error and offending line are the only useful part of a 400."""
    _seen, transport = _recorder(status=400, body="Parse error on line 2: bad IRI")
    store = OxigraphStore("http://oxigraph:7878", transport=transport)

    with pytest.raises(StoreWriteError, match="bad IRI"):
        asyncio.run(store.load_nquads(NQUADS))


def test_an_unreachable_store_raises_unavailable_not_write_error() -> None:
    """The two are distinguished because they mean different things.

    Unreachable is an operational condition the run degrades through; a
    rejected payload is a bug in what we produced.
    """

    def handler(_request: httpx.Request) -> httpx.Response:
        message = "connection refused"
        raise httpx.ConnectError(message)

    store = OxigraphStore(
        "http://oxigraph:7878",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(StoreUnavailableError, match="unreachable"):
        asyncio.run(store.load_nquads(NQUADS))


# --------------------------------------------------------------------------
# the read-back path — how a write is verified
# --------------------------------------------------------------------------


def test_named_graphs_reports_a_triple_count_per_graph() -> None:
    body = _results(
        [
            _count_binding("urn:pulse:output:r1:github", GITHUB_TRIPLES),
            _count_binding("urn:pulse:output:r1:ror", ROR_TRIPLES),
        ],
    )
    seen, transport = _recorder(status=200, body=body)
    store = OxigraphStore("http://oxigraph:7878", transport=transport)

    graphs = asyncio.run(store.named_graphs())

    assert graphs == {
        "urn:pulse:output:r1:github": GITHUB_TRIPLES,
        "urn:pulse:output:r1:ror": ROR_TRIPLES,
    }
    assert seen[0].url.path == "/query"
    assert seen[0].headers["accept"] == "application/sparql-results+json"


def test_a_missing_graph_counts_zero_rather_than_raising() -> None:
    _seen, transport = _recorder(status=200, body=_results([]))
    store = OxigraphStore("http://oxigraph:7878", transport=transport)

    count = asyncio.run(store.graph_triple_count("urn:pulse:output:nope:github"))

    assert count == 0


def test_is_available_is_false_rather_than_raising() -> None:
    """The health endpoint must not turn a down store into a 500."""

    def handler(_request: httpx.Request) -> httpx.Response:
        message = "connection refused"
        raise httpx.ConnectError(message)

    down = OxigraphStore("http://oxigraph:7878", transport=httpx.MockTransport(handler))
    _seen, transport = _recorder(status=200, body=_results([]))
    up = OxigraphStore("http://oxigraph:7878", transport=transport)

    assert asyncio.run(down.is_available()) is False
    assert asyncio.run(up.is_available()) is True


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "   "])
def test_no_url_configured_means_no_store(value: str | None) -> None:
    """Returning None is what makes the writer optional.

    The substrate is still projected and returned when no store is configured;
    it just is not written anywhere. Raising here would make an unconfigured
    deployment fail every extraction.
    """
    assert store_from_config(value) is None


def test_a_configured_url_is_stripped_of_whitespace_and_trailing_slash() -> None:
    store = store_from_config("  http://oxigraph:7878/  ")

    assert store is not None
    assert store.base_url == "http://oxigraph:7878"


def test_summarise_is_stable_for_logs() -> None:
    assert summarise({"b": 2, "a": 1}) == "a=1 b=2"
