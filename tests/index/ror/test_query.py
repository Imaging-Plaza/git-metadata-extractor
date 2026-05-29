from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from src.index.ror.config import QdrantConfig, RcpConfig, RorIndexConfig, ScopeConfig
from src.index.ror.models import DumpMatch
from src.index.ror.paths import ror_data_dir
from src.index.ror.query import lookup_dump, query, query_rag
from src.index.ror.rerank import RerankResult
from src.index.ror.storage.duckdb_store import RorStore, extract_record_columns


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("INDEX_DATA_DIR", str(tmp_path))
    yield tmp_path


def _make_cfg(mode="epfl_ethz"):
    return RorIndexConfig(
        rcp=RcpConfig(
            base_url="http://mock",
            embedding_model="Qwen/Qwen3-Embedding-8B",
            embedding_dim=8,
            query_instruction="ignored",
            reranker_model="Qwen/Qwen3-Reranker-8B",
        ),
        scope=ScopeConfig(mode=mode),
        qdrant=QdrantConfig(),
        data_dir=ror_data_dir().parent,
    )


def _seed_duckdb(mini_dump_path: Path) -> None:
    """Populate the DuckDB `records` table from the mini dump fixture."""
    records = json.loads(mini_dump_path.read_text(encoding="utf-8"))
    store = RorStore.open()
    try:
        store.bulk_replace_records(extract_record_columns(r) for r in records)
    finally:
        store.close()


class _FakeQdrantStore:
    """Stand-in for QdrantRorStore used to bypass network calls in tests."""

    def __init__(self, candidates):
        self.candidates = candidates

    def search(self, scope_mode, *, query_vector, top_k=50, country=None):
        return self.candidates[:top_k]


def test_query_rag_returns_top_hit_for_epfl(isolated):
    cfg = _make_cfg()
    fake_candidates = [
        {
            "ror_id": "https://ror.org/02s376052",
            "name": "École polytechnique fédérale de Lausanne",
            "text": "Name: EPFL",
            "record": {"id": "https://ror.org/02s376052"},
        },
        {
            "ror_id": "https://ror.org/05a28rw58",
            "name": "ETH Zurich",
            "text": "Name: ETH Zurich",
            "record": {"id": "https://ror.org/05a28rw58"},
        },
    ]

    async def fake_embed_query(rcp, text, *, normalize=True):
        return np.zeros(8, dtype=np.float32)

    async def fake_rerank(rcp, q, documents, *, top_n=None):
        # Reranker prefers EPFL (index 0) over ETHZ (index 1).
        return [
            RerankResult(index=0, score=0.91),
            RerankResult(index=1, score=0.42),
        ]

    with patch("src.index.ror.query.embed_query", side_effect=fake_embed_query), \
         patch("src.index.ror.query.rerank", side_effect=fake_rerank), \
         patch(
             "src.index.ror.query.QdrantRorStore",
             return_value=_FakeQdrantStore(fake_candidates),
         ):
        results = asyncio.run(query_rag(cfg, "EPFL", top_k=5, rerank_top_k=2))

    assert len(results) == 2
    assert results[0].ror_id == "https://ror.org/02s376052"
    assert results[0].score == pytest.approx(0.91)


def test_lookup_dump_finds_record_outside_embedded_subset(isolated, mini_dump_path):
    """Universität Bern is in the dump but not in the embedded EPFL/ETHZ subset.
    lookup_dump must still find it — proving the 'query the whole dump' path works."""
    cfg = _make_cfg()
    _seed_duckdb(mini_dump_path)

    results = lookup_dump(cfg, text="Universität Bern", country="CH", limit=5)
    ids = [r.ror_id for r in results]
    assert "https://ror.org/02k7v4d05" in ids


def test_lookup_dump_exact_ror_id(isolated, mini_dump_path):
    cfg = _make_cfg()
    _seed_duckdb(mini_dump_path)

    results = lookup_dump(cfg, ror_id="02s376052")
    assert len(results) == 1
    assert results[0].ror_id == "https://ror.org/02s376052"


def test_lookup_dump_requires_at_least_one_arg(isolated, mini_dump_path):
    cfg = _make_cfg()
    _seed_duckdb(mini_dump_path)
    with pytest.raises(ValueError):
        lookup_dump(cfg)


def test_query_auto_falls_back_to_lookup_when_no_rag_hits(isolated, mini_dump_path):
    """When semantic returns nothing above floor, auto mode falls back to lexical."""
    _seed_duckdb(mini_dump_path)
    cfg = _make_cfg()

    fake_candidates = [
        {
            "ror_id": "https://ror.org/02s376052",
            "name": "EPFL",
            "text": "Name: EPFL",
            "record": {},
        },
    ]

    async def fake_embed_query(rcp, text, *, normalize=True):
        return np.zeros(8, dtype=np.float32)

    async def fake_rerank(rcp, q, documents, *, top_n=None):
        # All candidates score below the floor → fallback should engage.
        return [RerankResult(index=0, score=-1.0)]

    with patch("src.index.ror.query.embed_query", side_effect=fake_embed_query), \
         patch("src.index.ror.query.rerank", side_effect=fake_rerank), \
         patch(
             "src.index.ror.query.QdrantRorStore",
             return_value=_FakeQdrantStore(fake_candidates),
         ):
        results = asyncio.run(query(cfg, "Universität Bern", mode="auto", score_floor=0.0))

    assert results
    assert all(isinstance(r, DumpMatch) for r in results)
    assert any(r.ror_id == "https://ror.org/02k7v4d05" for r in results)
