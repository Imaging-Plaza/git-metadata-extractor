"""rebuild_qdrant_from_chunks: SQL JOIN, payload shape, chunk_id as point id."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from src.index.openalex.embed import pipeline


def _seed_work_with_chunks(store, *, work_id: str, title: str, year: int, n_chunks: int):
    store.upsert_work(
        {
            "openalex_id": work_id,
            "doi": None,
            "title": title,
            "abstract": "abs",
            "publication_year": year,
            "primary_topic_id": None,
            "primary_source_id": None,
        },
        raw={"id": work_id},
    )
    for i in range(n_chunks):
        store.upsert_chunk(
            chunk_id=f"chunk-{work_id}-{i}",
            entity_type="works",
            entity_id=work_id,
            chunk_index=i,
            text=f"text-{i}",
            token_count=3,
            vector_id=f"chunk-{work_id}-{i}",
        )


def _seed_author_with_chunks(store, *, author_id: str, name: str, n_chunks: int):
    store.upsert_author(
        {
            "openalex_id": author_id,
            "display_name": name,
            "orcid": None,
            "last_known_institution_id": None,
        },
        raw={"id": author_id},
    )
    for i in range(n_chunks):
        store.upsert_chunk(
            chunk_id=f"chunk-{author_id}-{i}",
            entity_type="authors",
            entity_id=author_id,
            chunk_index=i,
            text=f"text-{i}",
            token_count=3,
            vector_id=f"chunk-{author_id}-{i}",
        )


class _FakeRCPClient:
    """Records calls so the test can assert on batching behaviour."""

    def __init__(self, *_args, batch_size: int | None = None, **_kw):
        self._batch_size = batch_size or 32
        self.calls: list[list[str]] = []

    @property
    def batch_size(self) -> int:
        return self._batch_size

    async def embed_all(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[float(i)] * 4 for i, _ in enumerate(texts)]


class _FakeQdrant:
    def __init__(self, *_args, **_kw):
        self.collections: set[str] = set()
        self.upserts: list[dict[str, Any]] = []

    def ensure_collection(self, name: str) -> None:
        self.collections.add(name)

    def upsert_points(
        self,
        collection: str,
        *,
        ids: list[str],
        vectors: list[list[float]],
        payloads: list[dict[str, Any]],
    ) -> None:
        self.upserts.append(
            {"collection": collection, "ids": list(ids), "payloads": list(payloads)},
        )


@pytest.fixture()
def fake_clients(monkeypatch):
    fake_rcp = _FakeRCPClient()
    fake_qdrant = _FakeQdrant()
    monkeypatch.setattr(pipeline, "RCPEmbeddingClient", lambda *a, **k: fake_rcp)
    monkeypatch.setattr(pipeline, "QdrantStore", lambda *a, **k: fake_qdrant)
    return fake_rcp, fake_qdrant


@pytest.mark.openalex()
def test_rebuild_works_payload_includes_title_and_year(tmp_store, base_config, fake_clients):
    fake_rcp, fake_qdrant = fake_clients
    _seed_work_with_chunks(tmp_store, work_id="W1", title="Some Title", year=2024, n_chunks=2)

    summary = pipeline.rebuild_qdrant_from_chunks(
        config=base_config,
        store=tmp_store,
        entity_types=["works"],
    )

    assert summary == {"works": 2}
    assert "works" in fake_qdrant.collections
    upsert = fake_qdrant.upserts[0]
    assert upsert["collection"] == "works"
    assert upsert["ids"] == ["chunk-W1-0", "chunk-W1-1"]
    p0 = upsert["payloads"][0]
    assert p0["entity_type"] == "works"
    assert p0["entity_id"] == "W1"
    assert p0["openalex_id"] == "W1"
    assert p0["chunk_index"] == 0
    assert p0["title"] == "Some Title"
    assert p0["year"] == 2024
    assert "display_name" not in p0


@pytest.mark.openalex()
def test_rebuild_authors_payload_uses_display_name(tmp_store, base_config, fake_clients):
    _, fake_qdrant = fake_clients
    _seed_author_with_chunks(tmp_store, author_id="A1", name="Jane Doe", n_chunks=1)

    summary = pipeline.rebuild_qdrant_from_chunks(
        config=base_config,
        store=tmp_store,
        entity_types=["authors"],
    )

    assert summary == {"authors": 1}
    p = fake_qdrant.upserts[0]["payloads"][0]
    assert p["entity_type"] == "authors"
    assert p["display_name"] == "Jane Doe"
    assert "title" not in p
    assert "year" not in p


@pytest.mark.openalex()
def test_rebuild_batches_by_rcp_batch_size(tmp_store, base_config, monkeypatch):
    # 5 chunks, batch_size 2 → expect 3 RCP calls of sizes [2, 2, 1].
    _seed_work_with_chunks(tmp_store, work_id="W1", title="t", year=2024, n_chunks=5)

    fake_rcp = _FakeRCPClient(batch_size=2)
    fake_qdrant = _FakeQdrant()
    monkeypatch.setattr(pipeline, "RCPEmbeddingClient", lambda *a, **k: fake_rcp)
    monkeypatch.setattr(pipeline, "QdrantStore", lambda *a, **k: fake_qdrant)

    summary = pipeline.rebuild_qdrant_from_chunks(
        config=base_config,
        store=tmp_store,
        entity_types=["works"],
    )

    assert summary == {"works": 5}
    assert [len(call) for call in fake_rcp.calls] == [2, 2, 1]
    assert [len(u["ids"]) for u in fake_qdrant.upserts] == [2, 2, 1]


@pytest.mark.openalex()
def test_rebuild_skips_chunks_with_unknown_entity_id(tmp_store, base_config, fake_clients):
    # Chunk references a work that doesn't exist → JOIN drops it silently.
    tmp_store.upsert_chunk(
        chunk_id="orphan-1",
        entity_type="works",
        entity_id="W-missing",
        chunk_index=0,
        text="orphan",
        token_count=1,
        vector_id="orphan-1",
    )
    _seed_work_with_chunks(tmp_store, work_id="W1", title="t", year=2024, n_chunks=1)

    _, fake_qdrant = fake_clients
    summary = pipeline.rebuild_qdrant_from_chunks(
        config=base_config,
        store=tmp_store,
        entity_types=["works"],
    )

    assert summary == {"works": 1}
    assert fake_qdrant.upserts[0]["ids"] == ["chunk-W1-0"]


@pytest.mark.openalex()
def test_rebuild_no_chunks_returns_zero(tmp_store, base_config, fake_clients):
    _, fake_qdrant = fake_clients
    summary = pipeline.rebuild_qdrant_from_chunks(
        config=base_config,
        store=tmp_store,
        entity_types=["works", "authors"],
    )
    assert summary == {"works": 0, "authors": 0}
    assert fake_qdrant.upserts == []


@pytest.mark.openalex()
def test_rebuild_rejects_unknown_entity_type(tmp_store, base_config, fake_clients):
    with pytest.raises(ValueError, match="Unknown entity_type"):
        pipeline.rebuild_qdrant_from_chunks(
            config=base_config,
            store=tmp_store,
            entity_types=["nope"],
        )
