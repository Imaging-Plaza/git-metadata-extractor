"""Combined fast tests for OpenAlex / Zenodo / ORCID / ROR RAG providers."""
from __future__ import annotations

import asyncio
from typing import Any

import numpy as np

from open_pulse_sources.index.ror.rerank import RerankResult
from git_metadata_extractor.agents.llm.agent_tools.openalex_rag import make_openalex_rag_search_tool
from git_metadata_extractor.agents.llm.agent_tools.orcid_rag import make_orcid_rag_search_tool
from git_metadata_extractor.agents.llm.agent_tools.ror_rag import make_ror_rag_search_tool
from git_metadata_extractor.agents.llm.agent_tools.zenodo_rag import make_zenodo_rag_search_tool
from git_metadata_extractor.providers.openalex_rag import OpenAlexRagProvider
from git_metadata_extractor.providers.orcid_rag import OrcidRagProvider
from git_metadata_extractor.providers.ror_rag import RorRagProvider
from git_metadata_extractor.providers.zenodo_rag import ZenodoRagProvider


class _FakeClient:
    def __init__(self, *, exists: bool = True) -> None:
        self._exists = exists

    def collection_exists(self, name: str) -> bool:  # noqa: ARG002
        return self._exists


class _FakeOpenAlexStore:
    def __init__(self, hits: list[dict] | None = None, *, exists: bool = True) -> None:
        self._hits = hits or []
        self.client = _FakeClient(exists=exists)
        self.search_calls: list[dict] = []

    def search(self, collection, *, query_vector, top_k, filter_payload):
        self.search_calls.append({"collection": collection, "k": top_k, "filter": filter_payload})
        return list(self._hits)


class _FakeOrcidStore:
    def __init__(self, hits: list[dict] | None = None, *, exists: bool = True) -> None:
        self._hits = hits or []
        self.client = _FakeClient(exists=exists)
        self.search_calls: list[dict] = []

    def collection(self, entity_type: str) -> str:
        return f"orcid_epfl_{entity_type}"

    def search(self, entity_type, *, query_vector, top_k, filter_payload):
        self.search_calls.append({"entity": entity_type, "k": top_k, "filter": filter_payload})
        return list(self._hits)


class _FakeRorStore:
    def __init__(self, hits: list[dict] | None = None, *, exists: bool = True) -> None:
        self._hits = hits or []
        self.client = _FakeClient(exists=exists)
        self.search_calls: list[dict] = []

    def collection_name(self, scope_mode: str) -> str:
        return f"ror_{scope_mode}"

    def search(self, scope_mode, *, query_vector, top_k, country=None):
        self.search_calls.append({"scope": scope_mode, "k": top_k, "country": country})
        return list(self._hits)


class _FakeEmbedder:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed_all(self, inputs):
        self.calls.append(list(inputs))
        return [[0.1, 0.2, 0.3] for _ in inputs]


class _FakeReranker:
    def __init__(self, results=None) -> None:
        self.results = results or []
        self.calls: list[dict] = []

    async def rerank(self, query, documents, *, top_n=None):
        self.calls.append({"q": query, "n": len(documents), "top_n": top_n})
        return list(self.results)


def _run(coro):
    return asyncio.run(coro)


# -- OpenAlex ---------------------------------------------------------------


def test_openalex_works_returns_thin_hits_with_snippet() -> None:
    hits = [{"id": "w1", "score": 0.9, "payload": {
        "openalex_id": "W1", "title": "Hallmarks of Cancer",
        "abstract": "X" * 500, "year": 2022, "doi": "10.1/x",
    }}]
    store = _FakeOpenAlexStore(hits)
    p = OpenAlexRagProvider(store=store, embedder=_FakeEmbedder(), reranker=None)
    out = _run(p.search("cancer", collection="works", top_k=3))
    assert out[0]["openalex_id"] == "W1"
    assert out[0]["title"] == "Hallmarks of Cancer"
    assert out[0]["snippet"] is not None
    assert len(out[0]["snippet"]) <= 320
    assert "abstract" not in out[0]  # not in thin keys


def test_openalex_filter_year_range() -> None:
    store = _FakeOpenAlexStore([])
    p = OpenAlexRagProvider(store=store, embedder=_FakeEmbedder(), reranker=None)
    _run(p.search("x", collection="works", filters={"year": {"$gte": 2020}}))
    assert store.search_calls[0]["filter"] == {"year": {"gte": 2020}}


def test_openalex_collection_missing_returns_empty() -> None:
    store = _FakeOpenAlexStore([{"id": "x"}], exists=False)
    p = OpenAlexRagProvider(store=store, embedder=_FakeEmbedder(), reranker=None)
    assert _run(p.search("q", collection="works")) == []
    assert store.search_calls == []


def test_openalex_unknown_collection_returns_empty() -> None:
    store = _FakeOpenAlexStore([{"id": "x"}])
    p = OpenAlexRagProvider(store=store, embedder=_FakeEmbedder(), reranker=None)
    assert _run(p.search("q", collection="bogus")) == []  # type: ignore[arg-type]


def test_openalex_search_tool_factory() -> None:
    store = _FakeOpenAlexStore([{"id": "w1", "score": 0.9, "payload": {"openalex_id": "W1", "title": "T"}}])
    p = OpenAlexRagProvider(store=store, embedder=_FakeEmbedder(), reranker=None)
    tool = make_openalex_rag_search_tool(p)
    assert tool.name == "search_openalex_rag"
    rows = _run(tool.function("q", "works", 3, None, False))
    assert rows[0]["openalex_id"] == "W1"


# -- Zenodo -----------------------------------------------------------------


def test_zenodo_search_thin_hits() -> None:
    hits = [{"id": "z1", "score": 0.85, "payload": {
        "zenodo_id": "12345", "title": "Dataset Foo",
        "doi": "10.5281/zenodo.12345", "year": 2024,
        "resource_type": "dataset", "access_right": "open",
    }}]
    store = _FakeOpenAlexStore(hits)
    p = ZenodoRagProvider(store=store, embedder=_FakeEmbedder(), reranker=None)
    out = _run(p.search("dataset", top_k=3))
    assert out[0]["zenodo_id"] == "12345"
    assert out[0]["resource_type"] == "dataset"
    assert out[0]["collection"] == "zenodo_records"


def test_zenodo_collection_missing() -> None:
    store = _FakeOpenAlexStore([{"id": "x"}], exists=False)
    p = ZenodoRagProvider(store=store, embedder=_FakeEmbedder(), reranker=None)
    assert _run(p.search("q")) == []
    assert store.search_calls == []


def test_zenodo_search_tool_factory() -> None:
    store = _FakeOpenAlexStore([{"id": "z1", "score": 0.9, "payload": {"zenodo_id": "12", "title": "T"}}])
    p = ZenodoRagProvider(store=store, embedder=_FakeEmbedder(), reranker=None)
    tool = make_zenodo_rag_search_tool(p)
    assert tool.name == "search_zenodo_rag"
    rows = _run(tool.function("q", 3, None, False))
    assert rows[0]["zenodo_id"] == "12"


# -- ORCID -----------------------------------------------------------------


def test_orcid_persons_thin_hits_with_snippet() -> None:
    hits = [{"id": "p1", "score": 0.9, "payload": {
        "orcid_id": "0000-0001",
        "display_name": "Alice",
        "biography": "B" * 500,
        "in_scope": True,
    }}]
    store = _FakeOrcidStore(hits)
    p = OrcidRagProvider(store=store, embedder=_FakeEmbedder(), reranker=None)
    out = _run(p.search("alice"))
    assert out[0]["orcid_id"] == "0000-0001"
    assert out[0]["display_name"] == "Alice"
    assert out[0]["snippet"] is not None
    assert "biography" not in out[0]


def test_orcid_unknown_entity_type_returns_empty() -> None:
    store = _FakeOrcidStore([{"id": "x"}])
    p = OrcidRagProvider(store=store, embedder=_FakeEmbedder(), reranker=None)
    assert _run(p.search("q", entity_type="bogus")) == []  # type: ignore[arg-type]


def test_orcid_collection_missing() -> None:
    store = _FakeOrcidStore([{"id": "x"}], exists=False)
    p = OrcidRagProvider(store=store, embedder=_FakeEmbedder(), reranker=None)
    assert _run(p.search("q", entity_type="persons")) == []


def test_orcid_search_tool_factory() -> None:
    hits = [{"id": "p1", "score": 0.9, "payload": {"orcid_id": "0000-0001", "display_name": "Alice"}}]
    store = _FakeOrcidStore(hits)
    p = OrcidRagProvider(store=store, embedder=_FakeEmbedder(), reranker=None)
    tool = make_orcid_rag_search_tool(p)
    assert tool.name == "search_orcid_rag"
    rows = _run(tool.function("q", "persons", 3, None, False))
    assert rows[0]["orcid_id"] == "0000-0001"


# -- ROR --------------------------------------------------------------------


class _FakeRorEmbedder:
    """Module-style: ROR uses functional embed_query() returning np.ndarray."""
    def __init__(self) -> None:
        self.calls = 0

    async def embed_query(self, _rcp, _text):  # noqa: D401, ARG002
        self.calls += 1
        return np.asarray([0.1, 0.2, 0.3], dtype=np.float32)


def _patched_ror(monkeypatch, embed_fn=None, rerank_fn=None) -> None:
    if embed_fn is not None:
        monkeypatch.setattr(
            "git_metadata_extractor.providers.ror_rag.embed_query", embed_fn,
        )
    if rerank_fn is not None:
        monkeypatch.setattr(
            "git_metadata_extractor.providers.ror_rag.rerank", rerank_fn,
        )


def test_ror_search_thin_hits(monkeypatch) -> None:
    async def fake_embed(_rcp, _text):
        return np.asarray([0.1, 0.2], dtype=np.float32)
    _patched_ror(monkeypatch, embed_fn=fake_embed)

    raw_hits = [{
        "score": 0.9, "ror_id": "https://ror.org/02s376052",
        "name": "EPFL", "text": "Name: EPFL\nTypes: education",
        "record": {
            "types": ["education"],
            "locations": [{"geonames_details": {"country_code": "CH"}}],
        },
    }]
    store = _FakeRorStore(raw_hits)
    rcp = type("RcpStub", (), {})()
    p = RorRagProvider(store=store, rcp=rcp)

    out = _run(p.search("epfl", scope_mode="epfl_ethz", top_k=3))
    assert out[0]["ror_id"] == "https://ror.org/02s376052"
    assert out[0]["name"] == "EPFL"
    assert out[0]["country_code"] == "CH"
    assert out[0]["scope_mode"] == "epfl_ethz"
    assert "snippet" in out[0]


def test_ror_country_filter_extracted(monkeypatch, caplog) -> None:
    async def fake_embed(_rcp, _text):
        return np.asarray([0.1], dtype=np.float32)
    _patched_ror(monkeypatch, embed_fn=fake_embed)

    store = _FakeRorStore([])
    p = RorRagProvider(store=store, rcp=type("R", (), {})())
    caplog.set_level("WARNING")
    _run(p.search("ch lab", filters={"country_code": "CH", "year": 2024}))
    assert store.search_calls[0]["country"] == "CH"
    assert any("dropped" in r.message for r in caplog.records)


def test_ror_country_eq_operator(monkeypatch) -> None:
    async def fake_embed(_rcp, _text):
        return np.asarray([0.1], dtype=np.float32)
    _patched_ror(monkeypatch, embed_fn=fake_embed)

    store = _FakeRorStore([])
    p = RorRagProvider(store=store, rcp=type("R", (), {})())
    _run(p.search("x", filters={"country_code": {"$eq": "FR"}}))
    assert store.search_calls[0]["country"] == "FR"


def test_ror_collection_missing(monkeypatch) -> None:
    store = _FakeRorStore([{"score": 0.9}], exists=False)
    p = RorRagProvider(store=store, rcp=type("R", (), {})())
    assert _run(p.search("q", scope_mode="worldwide")) == []
    assert store.search_calls == []


def test_ror_rerank_reorders(monkeypatch) -> None:
    async def fake_embed(_rcp, _text):
        return np.asarray([0.1], dtype=np.float32)

    async def fake_rerank(_rcp, _q, _docs, *, top_n=None):  # noqa: ARG001
        return [
            RerankResult(index=2, score=0.99),
            RerankResult(index=0, score=0.7),
        ]
    _patched_ror(monkeypatch, embed_fn=fake_embed, rerank_fn=fake_rerank)

    raw_hits = [
        {"score": 0.5, "ror_id": "a", "name": "A", "text": "alpha", "record": {}},
        {"score": 0.4, "ror_id": "b", "name": "B", "text": "beta", "record": {}},
        {"score": 0.3, "ror_id": "c", "name": "C", "text": "gamma", "record": {}},
    ]
    store = _FakeRorStore(raw_hits)
    p = RorRagProvider(store=store, rcp=type("R", (), {})())
    out = _run(p.search("q", top_k=2, rerank=True))
    assert [h["ror_id"] for h in out] == ["c", "a"]


def test_ror_search_tool_factory(monkeypatch) -> None:
    async def fake_embed(_rcp, _text):
        return np.asarray([0.1], dtype=np.float32)
    _patched_ror(monkeypatch, embed_fn=fake_embed)

    raw_hits = [{"score": 0.9, "ror_id": "X", "name": "X", "text": "x", "record": {}}]
    store = _FakeRorStore(raw_hits)
    p = RorRagProvider(store=store, rcp=type("R", (), {})())
    tool = make_ror_rag_search_tool(p)
    assert tool.name == "search_ror_rag"
    rows = _run(tool.function("q", "worldwide", 3, None, False))
    assert rows[0]["ror_id"] == "X"
