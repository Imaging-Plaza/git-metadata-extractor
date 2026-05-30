from __future__ import annotations

import asyncio

from src.v2.agents.llm.agent_tools.huggingface_rag import (
    make_huggingface_rag_search_tool,
)
from src.v2.ingest.providers.huggingface_rag import HuggingFaceRagProvider


class _FakeClient:
    def __init__(self, *, exists: bool = True) -> None:
        self._exists = exists

    def collection_exists(self, name: str) -> bool:  # noqa: ARG002
        return self._exists


class _FakeStore:
    def __init__(self, hits: list[dict] | None = None, *, exists: bool = True) -> None:
        self._hits = hits or []
        self.client = _FakeClient(exists=exists)
        self.search_calls: list[dict] = []

    def search(self, collection, *, query_vector, top_k, filter_payload):
        self.search_calls.append({
            "collection": collection, "top_k": top_k, "filter": filter_payload,
        })
        return list(self._hits)


class _FakeEmbedder:
    def __init__(self, vector=(0.1, 0.2, 0.3)) -> None:
        self.vector = list(vector)
        self.calls: list[list[str]] = []

    async def embed_all(self, inputs):
        self.calls.append(list(inputs))
        return [self.vector for _ in inputs]


class _FakeReranker:
    def __init__(self, results=None) -> None:
        self.results = results or []
        self.calls: list[dict] = []

    async def rerank(self, query, documents, *, top_n=None):
        self.calls.append({
            "query": query, "n": len(documents), "top_n": top_n,
        })
        return list(self.results)


def _run(coro):
    return asyncio.run(coro)


def test_search_returns_thin_model_hits() -> None:
    hits = [{
        "id": "h1", "score": 0.9,
        "payload": {
            "entity_type": "model",
            "repo_id": "ZurichNLP/swissbert",
            "author": "ZurichNLP",
            "library_name": "transformers",
            "pipeline_tag": "fill-mask",
            "downloads": 1000,
            "likes": 5,
        },
    }]
    store = _FakeStore(hits)
    emb = _FakeEmbedder()
    p = HuggingFaceRagProvider(store=store, embedder=emb, reranker=None)

    out = _run(p.search("swiss german model", collection="models", top_k=5))

    assert store.search_calls[0]["collection"] == "huggingface_models"
    assert emb.calls == [["swiss german model"]]
    assert out[0]["repo_id"] == "ZurichNLP/swissbert"
    assert out[0]["library_name"] == "transformers"
    assert out[0]["collection"] == "models"
    assert "entity_type" not in out[0]  # not in thin keys


def test_search_returns_empty_when_collection_missing() -> None:
    store = _FakeStore([{"id": "x"}], exists=False)
    p = HuggingFaceRagProvider(store=store, embedder=_FakeEmbedder(), reranker=None)
    assert _run(p.search("q", collection="orgs")) == []
    # search not invoked when collection missing
    assert store.search_calls == []


def test_search_respects_filter_allowlist_and_translation() -> None:
    store = _FakeStore([])
    p = HuggingFaceRagProvider(store=store, embedder=_FakeEmbedder(), reranker=None)

    _run(p.search(
        "x",
        filters={
            "library_name": "transformers",
            "downloads": {"$gte": 100, "$lte": 1000},
            "secret_field": "boom",
        },
    ))
    fp = store.search_calls[0]["filter"]
    assert fp == {
        "library_name": "transformers",
        "downloads": {"gte": 100, "lte": 1000},
    }


def test_search_unknown_collection_returns_empty() -> None:
    store = _FakeStore([{"id": "x"}])
    p = HuggingFaceRagProvider(store=store, embedder=_FakeEmbedder(), reranker=None)
    assert _run(p.search("q", collection="not_a_collection")) == []  # type: ignore[arg-type]


def test_search_blank_query_returns_empty() -> None:
    store = _FakeStore([{"id": "x"}])
    emb = _FakeEmbedder()
    p = HuggingFaceRagProvider(store=store, embedder=emb, reranker=None)
    assert _run(p.search("   ")) == []
    assert emb.calls == []
    assert store.search_calls == []


def test_rerank_reorders_via_synthetic_doc_strings() -> None:
    hits = [
        {"id": "a", "score": 0.5, "payload": {"repo_id": "x/alpha", "library_name": "transformers"}},
        {"id": "b", "score": 0.4, "payload": {"repo_id": "x/beta", "library_name": "diffusers"}},
        {"id": "c", "score": 0.3, "payload": {"repo_id": "x/gamma"}},
    ]
    store = _FakeStore(hits)
    rer = _FakeReranker([
        {"index": 1, "relevance_score": 0.99},
        {"index": 0, "relevance_score": 0.5},
    ])
    p = HuggingFaceRagProvider(store=store, embedder=_FakeEmbedder(), reranker=rer)

    out = _run(p.search("q", collection="models", top_k=2, rerank=True))

    # candidate_k expands beyond top_k
    assert store.search_calls[0]["top_k"] >= 30
    assert rer.calls and rer.calls[0]["n"] == 3
    ids = [r["id"] for r in out]
    assert ids == ["b", "a"]


def test_search_tool_factory() -> None:
    store = _FakeStore([{"id": "h1", "score": 0.9, "payload": {"repo_id": "x/y"}}])
    p = HuggingFaceRagProvider(store=store, embedder=_FakeEmbedder(), reranker=None)
    tool = make_huggingface_rag_search_tool(p)
    assert tool.name == "search_huggingface_rag"
    rows = _run(tool.function("q", "models", 3, None, False))
    assert rows[0]["repo_id"] == "x/y"
