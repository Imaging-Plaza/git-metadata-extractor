from __future__ import annotations

import logging

import pytest

from src.v2.ingest.providers._rag_helpers import (
    apply_rerank_indices,
    expand_candidate_k,
    filter_allowlist,
    make_snippet,
    thin_payload,
    to_simple_filter_payload,
)


def test_filter_allowlist_drops_disallowed_keys(caplog) -> None:
    caplog.set_level(logging.WARNING)
    out = filter_allowlist(
        {"year": 2024, "secret": "x", "doi": "10.1/x"},
        frozenset({"year", "doi"}),
        log_label="test",
    )
    assert out == {"year": 2024, "doi": "10.1/x"}
    assert any("dropped non-allowlisted filter keys" in r.message for r in caplog.records)


def test_filter_allowlist_returns_none_when_empty() -> None:
    assert filter_allowlist(None, frozenset({"x"}), log_label="t") is None
    assert filter_allowlist({}, frozenset({"x"}), log_label="t") is None
    # All keys dropped → None
    assert filter_allowlist({"a": 1}, frozenset({"b"}), log_label="t") is None


def test_to_simple_filter_payload_translates_operators() -> None:
    out = to_simple_filter_payload({
        "year": {"$gte": 2020, "$lte": 2024},
        "doi": "10.1/x",
        "tags": {"$in": ["a", "b"]},
        "openalex_id": {"$eq": "W1"},
        "names": ["x", "y"],
    })
    assert out == {
        "year": {"gte": 2020, "lte": 2024},
        "doi": "10.1/x",
        "tags": ["a", "b"],
        "openalex_id": "W1",
        "names": ["x", "y"],
    }


def test_to_simple_filter_payload_drops_unsupported_operators(caplog) -> None:
    caplog.set_level(logging.WARNING)
    out = to_simple_filter_payload({
        "year": 2024,
        "tag": {"$ne": "draft"},
        "abstract": {"$contains": "foo"},
    })
    assert out == {"year": 2024}
    assert sum(1 for r in caplog.records if "unsupported operator" in r.message) == 2


def test_to_simple_filter_payload_handles_empty() -> None:
    assert to_simple_filter_payload(None) is None
    assert to_simple_filter_payload({}) is None


def test_expand_candidate_k_floor_and_multiplier() -> None:
    assert expand_candidate_k(3) == 30  # floor wins
    assert expand_candidate_k(10) == 50  # multiplier wins
    assert expand_candidate_k(20, multiplier=10, floor=5) == 200


def test_apply_rerank_indices_reorders() -> None:
    hits = [
        {"id": "a", "score": 0.5, "payload": {}},
        {"id": "b", "score": 0.4, "payload": {}},
        {"id": "c", "score": 0.3, "payload": {}},
    ]
    rerank = [
        {"index": 2, "relevance_score": 0.99},
        {"index": 0, "relevance_score": 0.7},
    ]
    out = apply_rerank_indices(hits, rerank, top_k=2)
    assert [h["id"] for h in out] == ["c", "a"]
    assert out[0]["score"] == pytest.approx(0.99)


def test_apply_rerank_indices_empty_falls_back() -> None:
    hits = [{"id": "a", "score": 0.5}]
    assert apply_rerank_indices(hits, [], top_k=1) == [{"id": "a", "score": 0.5}]


def test_apply_rerank_indices_skips_invalid_index() -> None:
    hits = [{"id": "a"}]
    rerank = [{"index": 99, "relevance_score": 0.9}]
    assert apply_rerank_indices(hits, rerank, top_k=5) == []


def test_make_snippet_short() -> None:
    assert make_snippet("hi") == "hi"
    assert make_snippet("") is None
    assert make_snippet(None) is None
    assert make_snippet(123) is None  # type: ignore[arg-type]


def test_make_snippet_truncates() -> None:
    long = "x" * 1000
    out = make_snippet(long, max_chars=100)
    assert out is not None
    assert len(out) == 100
    assert out.endswith("…")


def test_thin_payload_keeps_keys_and_extras() -> None:
    payload = {"name": "foo", "year": 2024, "skip": "x"}
    out = thin_payload(payload, ("name", "year"), extras={"id": "p1", "score": 0.9, "drop": None})
    assert out == {"name": "foo", "year": 2024, "id": "p1", "score": 0.9}


def test_thin_payload_drops_none_keys() -> None:
    payload = {"name": "foo", "year": None}
    out = thin_payload(payload, ("name", "year"))
    assert out == {"name": "foo"}
