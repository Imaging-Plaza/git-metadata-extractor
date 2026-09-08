"""Behavioural tests for the shared single-collection RAG provider.

The RAG path needs Qdrant, which is absent in dev and CI, so the corpus differ
reports an empty diff whether a refactor here is right or wrong. These tests
drive the provider against a fake store instead, and pin the four things that
would otherwise change silently:

1. which collection is queried,
2. which filter keys survive the allowlist,
3. which payload keys survive the thin-key projection,
4. what text the reranker is asked to score.

(4) is not hypothetical. Collapsing `github_rag` onto this shared provider, the
`_rerank_text` body was reconstructed from its docstring and came out as
`owner/name + description` instead of `repo_id + description`. Every existing
test passed, and the corpus diff was empty, because neither exercises the
reranker. An A/B run against the pre-refactor module caught it.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from git_metadata_extractor.providers.epfl_graph_rag import EpflGraphRagProvider
from git_metadata_extractor.providers.github_rag import GitHubRagProvider
from git_metadata_extractor.providers.swissubase_rag import SwissubaseRagProvider

TOP_K = 2
HITS = 3

PAYLOAD: dict[str, Any] = {
    "repo_id": "octocat/Hello-World",
    "owner": "octocat",
    "name": "Hello-World",
    "description": "a repo",
    "primary_language": "Python",
    "license_spdx": "MIT",
    "stars": 7,
    "forks": 2,
    "pushed_at": "2024-01-01",
    "is_archived": False,
    "category_id": "cat-1",
    "depth": 2,
    "embedding_text": "discipline text",
    "not_a_thin_key": "must be dropped",
}


class _FakeClient:
    def __init__(self, *, exists: bool = True) -> None:
        self.exists = exists

    def collection_exists(self, _name: str) -> bool:
        return self.exists


class _FakeStore:
    def __init__(self, *, exists: bool = True, hits: int = 3) -> None:
        self.client = _FakeClient(exists=exists)
        self.calls: list[dict[str, Any]] = []
        self._hits = hits

    def search(
        self,
        collection: str,
        *,
        query_vector: list[float],  # noqa: ARG002 — recorded shape, unused
        top_k: int,
        filter_payload: Any,
    ) -> list[dict[str, Any]]:
        self.calls.append(
            {
                "collection": collection,
                "top_k": top_k,
                "filter_payload": filter_payload,
            },
        )
        return [
            {"id": f"h{i}", "score": 1.0 - i / 10, "payload": dict(PAYLOAD)}
            for i in range(self._hits)
        ]


class _FakeEmbedder:
    def __init__(self, *, vectors: list[list[float]] | None = None) -> None:
        self.vectors = [[0.1, 0.2]] if vectors is None else vectors

    async def embed_all(self, _texts: list[str]) -> list[list[float]]:
        return self.vectors


class _FakeReranker:
    def __init__(self) -> None:
        self.documents: list[str] | None = None

    async def rerank(
        self,
        _query: str,
        documents: list[str],
        *,
        top_n: int,
    ) -> list[dict[str, Any]]:
        self.documents = documents
        return [{"index": i, "score": 1.0} for i in range(min(top_n, len(documents)))]


def _run(provider_cls, *, store=None, reranker=None, **kwargs):
    store = store or _FakeStore()
    reranker = reranker if reranker is not None else _FakeReranker()
    provider = provider_cls(
        store=store,
        embedder=_FakeEmbedder(),
        reranker=reranker,
    )
    out = asyncio.run(provider.search("some query", **kwargs))
    return out, store, reranker


# --------------------------------------------------------------------------
# collection routing and the thin-key projection
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("provider_cls", "expected_collection"),
    [
        (GitHubRagProvider, "github_repos"),
        (EpflGraphRagProvider, "epfl_graph_disciplines"),
    ],
)
def test_search_queries_the_specs_collection(
    provider_cls: type,
    expected_collection: str,
) -> None:
    _out, store, _rr = _run(provider_cls, top_k=TOP_K)

    assert store.calls[0]["collection"] == expected_collection
    # And the collection is reported back on every hit.
    assert {hit["collection"] for hit in _out} == {expected_collection}


def test_projection_keeps_spec_keys_and_drops_the_rest() -> None:
    out, _store, _rr = _run(GitHubRagProvider, top_k=1)

    assert out[0]["repo_id"] == "octocat/Hello-World"
    assert out[0]["stars"] == PAYLOAD["stars"]
    assert "not_a_thin_key" not in out[0]
    # `category_id` is an EPFL Graph key, not a GitHub one.
    assert "category_id" not in out[0]
    # id and score ride along as extras.
    assert out[0]["id"] == "h0"
    assert out[0]["score"] == pytest.approx(1.0)


def test_each_index_projects_its_own_keys() -> None:
    github, _s, _r = _run(GitHubRagProvider, top_k=1)
    epfl, _s2, _r2 = _run(EpflGraphRagProvider, top_k=1)

    assert "repo_id" in github[0]
    assert "repo_id" not in epfl[0]
    assert "category_id" in epfl[0]
    assert "category_id" not in github[0]


# --------------------------------------------------------------------------
# the filter allowlist
# --------------------------------------------------------------------------


def test_non_allowlisted_filter_keys_are_dropped() -> None:
    _out, store, _rr = _run(
        GitHubRagProvider,
        top_k=1,
        filters={"owner": "octocat", "category_id": "cat-1", "BOGUS": "x"},
    )
    payload = store.calls[0]["filter_payload"]

    rendered = repr(payload)
    assert "octocat" in rendered
    # `category_id` belongs to a different index and `BOGUS` to none.
    assert "cat-1" not in rendered
    assert "BOGUS" not in rendered


# --------------------------------------------------------------------------
# rerank: candidate expansion and the document text
# --------------------------------------------------------------------------


def test_rerank_requests_more_candidates_than_top_k() -> None:
    """Reranking only helps if it has more than `top_k` to choose from."""
    _out, plain, _rr = _run(GitHubRagProvider, top_k=HITS, rerank=False)
    _out2, expanded, _rr2 = _run(GitHubRagProvider, top_k=HITS, rerank=True)

    assert plain.calls[0]["top_k"] == HITS
    assert expanded.calls[0]["top_k"] > HITS


def test_github_reranks_on_repo_id_and_description() -> None:
    """Pins the exact bug an earlier version of this collapse introduced."""
    _out, _store, reranker = _run(GitHubRagProvider, top_k=TOP_K, rerank=True)

    assert reranker.documents is not None
    # Explicitly `repo_id + description`, not the `owner/name` form an earlier
    # version of this collapse produced.
    assert reranker.documents[0] == "octocat/Hello-World\na repo"


def test_epfl_graph_reranks_on_name_and_embedding_text() -> None:
    _out, _store, reranker = _run(EpflGraphRagProvider, top_k=TOP_K, rerank=True)

    assert reranker.documents is not None
    # `name` is absent from the fake payload, so it falls back to category_id.
    assert reranker.documents[0] == "Hello-World\ndiscipline text"


def test_rerank_is_skipped_without_a_reranker() -> None:
    out, store, _rr = _run(SwissubaseRagProvider, reranker=None, top_k=TOP_K, rerank=True)

    assert len(out) == TOP_K
    # Still asked for the expanded candidate set, then truncated locally.
    assert store.calls[0]["top_k"] > TOP_K


# --------------------------------------------------------------------------
# graceful degradation — a RAG index is an enrichment, never a dependency
# --------------------------------------------------------------------------


def test_missing_collection_returns_empty_without_searching() -> None:
    out, store, _rr = _run(GitHubRagProvider, store=_FakeStore(exists=False), top_k=TOP_K)

    assert out == []
    assert store.calls == []


@pytest.mark.parametrize("query", ["", "   "])
def test_blank_query_short_circuits(query: str) -> None:
    store = _FakeStore()
    provider = GitHubRagProvider(
        store=store,
        embedder=_FakeEmbedder(),
        reranker=_FakeReranker(),
    )

    assert asyncio.run(provider.search(query)) == []
    assert store.calls == []


def test_search_failure_degrades_to_empty() -> None:
    class Boom(_FakeStore):
        def search(self, *_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
            message = "qdrant is down"
            raise RuntimeError(message)

    out, _store, _rr = _run(GitHubRagProvider, store=Boom(), top_k=TOP_K)

    assert out == []


def test_default_top_k_comes_from_the_spec() -> None:
    out, store, _rr = _run(GitHubRagProvider)

    assert store.calls[0]["top_k"] == GitHubRagProvider.SPEC.default_top_k
    assert len(out) <= GitHubRagProvider.SPEC.default_top_k
