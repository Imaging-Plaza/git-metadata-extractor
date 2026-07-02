from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

from open_pulse_sources.index.ethz_research_collection.rerank import RerankHit
from open_pulse_sources.index.ethz_research_collection.store import (
    ARTICLES_COLLECTION,
    CHUNKS_COLLECTION,
    ORGANIZATIONS_COLLECTION,
    PERSONS_COLLECTION,
)
from src.v2.agents.llm.agent_tools.ethz_research_collection_rag import (
    make_ethz_research_collection_rag_fetch_chunks_tool,
    make_ethz_research_collection_rag_fetch_records_tool,
    make_ethz_research_collection_rag_search_tool,
)
from src.v2.ingest.providers.ethz_research_collection_rag import EthzResearchCollectionRagProvider


class _FakeStore:
    """Duck-typed `QdrantStore` that records calls and returns canned data."""

    def __init__(self) -> None:
        self.search_calls: list[dict[str, Any]] = []
        self.scroll_calls: list[dict[str, Any]] = []
        self.lookup_calls: list[dict[str, Any]] = []
        self._search_returns: list[dict[str, Any]] = []
        self._scroll_returns: list[dict[str, Any]] = []
        self._lookup_returns: list[dict[str, Any]] = []

    def set_search(self, hits: list[dict[str, Any]]) -> None:
        self._search_returns = hits

    def set_scroll(self, recs: list[dict[str, Any]]) -> None:
        self._scroll_returns = recs

    def set_lookup(self, recs: list[dict[str, Any]]) -> None:
        self._lookup_returns = recs

    def search(self, collection: str, *, query_vector, top_k, query_filter):
        self.search_calls.append({
            "collection": collection,
            "vector": list(query_vector),
            "top_k": top_k,
            "filter": query_filter,
        })
        return list(self._search_returns)

    def scroll(self, collection: str, *, query_filter, limit):
        self.scroll_calls.append({
            "collection": collection,
            "filter": query_filter,
            "limit": limit,
        })
        return list(self._scroll_returns)

    def lookup(self, collection: str, *, ids):
        self.lookup_calls.append({"collection": collection, "ids": list(ids)})
        return list(self._lookup_returns)


class _FakeEmbedder:
    """Duck-typed `RCPEmbedder`; pretends to be an async context manager."""

    def __init__(self, vector: list[float] | None = None, fail: bool = False):
        self.vector = vector or [0.1, 0.2, 0.3]
        self.fail = fail
        self.entered = 0
        self.exited = 0
        self.embed_calls: list[str] = []

    async def __aenter__(self):
        self.entered += 1
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.exited += 1

    async def embed_query(self, query: str, instruction: str | None = None):
        self.embed_calls.append(query)
        if self.fail:
            from open_pulse_sources.index.ethz_research_collection.embed import EmbedError
            raise EmbedError("forced failure")
        return list(self.vector)


class _FakeReranker:
    def __init__(self, hits: list[RerankHit] | None = None, fail: bool = False):
        self._hits = hits or []
        self.fail = fail
        self.entered = 0
        self.calls: list[dict[str, Any]] = []

    async def __aenter__(self):
        self.entered += 1
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def rerank(self, query: str, documents: list[str], *, top_n=None):
        self.calls.append({
            "query": query,
            "documents": list(documents),
            "top_n": top_n,
        })
        if self.fail:
            from open_pulse_sources.index.ethz_research_collection.rerank import RerankError
            raise RerankError("forced failure")
        return list(self._hits)


def _make_provider(
    *,
    search_hits: list[dict[str, Any]] | None = None,
    scroll_recs: list[dict[str, Any]] | None = None,
    lookup_recs: list[dict[str, Any]] | None = None,
    embedder: _FakeEmbedder | None = None,
    reranker: _FakeReranker | None = None,
) -> tuple[EthzResearchCollectionRagProvider, _FakeStore, _FakeEmbedder, _FakeReranker | None]:
    store = _FakeStore()
    if search_hits is not None:
        store.set_search(search_hits)
    if scroll_recs is not None:
        store.set_scroll(scroll_recs)
    if lookup_recs is not None:
        store.set_lookup(lookup_recs)
    emb = embedder or _FakeEmbedder()
    rer = reranker
    provider = EthzResearchCollectionRagProvider(
        store=store,  # type: ignore[arg-type]
        embedder=emb,  # type: ignore[arg-type]
        reranker=rer,  # type: ignore[arg-type]
    )
    return provider, store, emb, rer


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


def test_search_returns_thin_chunk_hits_with_snippet() -> None:
    body_text = "This paper introduces a graph attention network. " * 20
    hits = [
        {
            "id": str(uuid.uuid4()),
            "score": 0.91,
            "payload": {
                "article_uuid": "article-1",
                "chunk_index": 3,
                "title": "GAT for Drug Discovery",
                "doi": "10.1234/foo",
                "year": 2024,
                "research_collection_url": "https://research-collection.ethz.ch/record/1",
                "text": body_text,
                "matched_urls": ["https://github.com/lab/repo"],
            },
        }
    ]
    provider, store, embedder, _rer = _make_provider(search_hits=hits)

    out = _run(provider.search("graph attention network"))

    assert embedder.entered == 1
    assert embedder.embed_calls == ["graph attention network"]
    assert store.search_calls[0]["collection"] == CHUNKS_COLLECTION
    assert store.search_calls[0]["top_k"] == 10
    assert len(out) == 1
    row = out[0]
    assert row["collection"] == "chunks"
    assert row["article_uuid"] == "article-1"
    assert row["chunk_index"] == 3
    assert row["title"] == "GAT for Drug Discovery"
    # Snippet truncates the long body and never exceeds the configured cap.
    assert "snippet" in row
    assert len(row["snippet"]) <= 320
    assert "text" not in row  # full body NOT returned by search


def test_search_drops_non_allowlisted_filter_keys(caplog) -> None:
    provider, store, _emb, _rer = _make_provider(search_hits=[])
    caplog.set_level("WARNING")

    _run(provider.search(
        "x",
        filters={"year": 2024, "secret_key": "boom", "doi": "10.1/x"},
    ))

    qfilter = store.search_calls[0]["filter"]
    assert qfilter is not None
    # Reach into the Qdrant Filter to confirm only allowlisted keys made it.
    keys = sorted(getattr(c, "key", None) for c in (qfilter.must or []))
    assert "year" in keys
    assert "doi" in keys
    assert "secret_key" not in keys
    assert any("dropped non-allowlisted filter keys" in r.message for r in caplog.records)


def test_search_resolves_collection_aliases() -> None:
    provider, store, _emb, _rer = _make_provider(search_hits=[])

    _run(provider.search("x", collection="articles"))
    _run(provider.search("x", collection="persons"))
    _run(provider.search("x", collection="organizations"))

    seen = [c["collection"] for c in store.search_calls]
    assert seen == [
        ARTICLES_COLLECTION,
        PERSONS_COLLECTION,
        ORGANIZATIONS_COLLECTION,
    ]


def test_search_returns_empty_on_blank_query() -> None:
    provider, store, embedder, _rer = _make_provider(search_hits=[{"id": "x"}])
    out = _run(provider.search("   "))
    assert out == []
    assert embedder.embed_calls == []
    assert store.search_calls == []


def test_search_returns_empty_on_embed_failure(caplog) -> None:
    provider, store, _emb, _rer = _make_provider(
        search_hits=[{"id": "x"}],
        embedder=_FakeEmbedder(fail=True),
    )
    caplog.set_level("WARNING")
    out = _run(provider.search("graph attention"))
    assert out == []
    assert store.search_calls == []  # never reached


def test_search_with_rerank_reorders_hits() -> None:
    hits = [
        {"id": "a", "score": 0.5, "payload": {"text": "alpha"}},
        {"id": "b", "score": 0.4, "payload": {"text": "beta"}},
        {"id": "c", "score": 0.3, "payload": {"text": "gamma"}},
    ]
    reranker = _FakeReranker(hits=[
        RerankHit(index=2, score=0.99),
        RerankHit(index=0, score=0.7),
    ])
    provider, store, _emb, _rer = _make_provider(
        search_hits=hits, reranker=reranker,
    )

    out = _run(provider.search("q", top_k=2, rerank=True))

    # candidate_k expands beyond top_k when rerank is on.
    assert store.search_calls[0]["top_k"] >= 30
    assert reranker is not None and reranker.calls
    ids = [row["id"] for row in out]
    assert ids == ["c", "a"]
    assert out[0]["score"] == pytest.approx(0.99)


def test_search_without_rerank_skips_reranker() -> None:
    reranker = _FakeReranker(hits=[RerankHit(index=0, score=0.99)])
    provider, _store, _emb, rer = _make_provider(
        search_hits=[{"id": "a", "score": 0.5, "payload": {}}],
        reranker=reranker,
    )

    _run(provider.search("q", rerank=False))

    assert rer is not None and rer.calls == []  # reranker never invoked
    assert rer.entered == 0


def test_search_falls_back_when_rerank_fails(caplog) -> None:
    reranker = _FakeReranker(fail=True)
    hits = [{"id": "a", "score": 0.5, "payload": {"text": "alpha"}}]
    provider, _store, _emb, _rer = _make_provider(
        search_hits=hits, reranker=reranker,
    )
    caplog.set_level("WARNING")

    out = _run(provider.search("q", top_k=1, rerank=True))

    assert [r["id"] for r in out] == ["a"]


# ---------------------------------------------------------------------------
# fetch_chunks
# ---------------------------------------------------------------------------


def test_fetch_chunks_orders_by_chunk_index() -> None:
    recs = [
        {"id": "p3", "payload": {"article_uuid": "A", "chunk_index": 2, "text": "t2"}},
        {"id": "p1", "payload": {"article_uuid": "A", "chunk_index": 0, "text": "t0"}},
        {"id": "p2", "payload": {"article_uuid": "A", "chunk_index": 1, "text": "t1"}},
    ]
    provider, store, _emb, _rer = _make_provider(scroll_recs=recs)

    out = _run(provider.fetch_chunks("A"))

    assert store.scroll_calls[0]["collection"] == CHUNKS_COLLECTION
    assert [c["chunk_index"] for c in out] == [0, 1, 2]
    assert [c["text"] for c in out] == ["t0", "t1", "t2"]


def test_fetch_chunks_blank_uuid_returns_empty() -> None:
    provider, store, _emb, _rer = _make_provider(scroll_recs=[{"id": "x", "payload": {}}])
    assert _run(provider.fetch_chunks("")) == []
    assert store.scroll_calls == []


# ---------------------------------------------------------------------------
# fetch_records
# ---------------------------------------------------------------------------


def test_fetch_records_returns_full_payload() -> None:
    recs = [
        {"id": "u1", "payload": {"name": "Person 1", "orcid": "0000-...-1"}},
        {"id": "u2", "payload": {"name": "Person 2"}},
    ]
    provider, store, _emb, _rer = _make_provider(lookup_recs=recs)

    out = _run(provider.fetch_records("persons", ["u1", "u2"]))

    assert store.lookup_calls[0]["collection"] == PERSONS_COLLECTION
    assert store.lookup_calls[0]["ids"] == ["u1", "u2"]
    assert [r["payload"]["name"] for r in out] == ["Person 1", "Person 2"]


def test_fetch_records_rejects_chunks_collection() -> None:
    provider, _store, _emb, _rer = _make_provider(lookup_recs=[])
    with pytest.raises(ValueError):
        _run(provider.fetch_records("chunks", ["x"]))


# ---------------------------------------------------------------------------
# Tool factories
# ---------------------------------------------------------------------------


def test_search_tool_factory_wires_provider() -> None:
    provider, _store, embedder, _rer = _make_provider(
        search_hits=[{"id": "a", "score": 0.5, "payload": {"text": "alpha"}}],
    )
    tool = make_ethz_research_collection_rag_search_tool(provider)

    assert tool.name == "search_ethz_research_collection_rag"
    rows = _run(tool.function("query", "chunks", 3, None, False))
    assert len(rows) == 1
    assert embedder.embed_calls == ["query"]


def test_fetch_chunks_tool_factory_wires_provider() -> None:
    provider, store, _emb, _rer = _make_provider(
        scroll_recs=[{"id": "x", "payload": {"article_uuid": "A", "chunk_index": 0, "text": "t"}}],
    )
    tool = make_ethz_research_collection_rag_fetch_chunks_tool(provider)

    assert tool.name == "fetch_ethz_research_collection_chunks"
    rows = _run(tool.function("A", 5))
    assert rows[0]["text"] == "t"
    assert store.scroll_calls[0]["limit"] == 5


def test_fetch_records_tool_factory_returns_empty_on_chunks_collection() -> None:
    provider, _store, _emb, _rer = _make_provider(lookup_recs=[])
    tool = make_ethz_research_collection_rag_fetch_records_tool(provider)

    assert tool.name == "fetch_ethz_research_collection_records"
    # The tool catches ValueError and returns [] so it's safe for the LLM.
    out = _run(tool.function("chunks", ["x"]))
    assert out == []


def test_fetch_records_tool_factory_returns_records() -> None:
    provider, _store, _emb, _rer = _make_provider(
        lookup_recs=[{"id": "u1", "payload": {"name": "P"}}],
    )
    tool = make_ethz_research_collection_rag_fetch_records_tool(provider)

    out = _run(tool.function("persons", ["u1"]))
    assert out[0]["payload"]["name"] == "P"
