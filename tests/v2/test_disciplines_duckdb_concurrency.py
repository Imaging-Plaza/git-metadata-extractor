# tests/v2/test_disciplines_duckdb_concurrency.py
"""Bug 01: _fetch_category_chain_qids must not fail under concurrency. With every
epfl_graph opener read-only, the disciplines lookup can run from many threads
alongside a resident read-only handle (stats / federated) without tripping
DuckDB's "different configuration than existing connections" error.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from src.index.epfl_graph.storage.duckdb_store import EpflGraphStore
from src.v2.pipeline.stages.rule_based_disciplines import _fetch_category_chain_qids


def _build(tmp_path: Path) -> Path:
    p = tmp_path / "epfl_graph.duckdb"
    store = EpflGraphStore.open(p)
    store.upsert_category({"category_id": "C1", "name": "root"}, {})
    store.upsert_category(
        {"category_id": "C2", "name": "child", "parent_id": "C1"}, {},
    )
    store.update_wikidata_qid("C2", "Q2")
    store.close()
    return p


def test_chain_walk_resolves(tmp_path: Path):
    p = _build(tmp_path)
    result = _fetch_category_chain_qids(["C2"], str(p))
    assert result["C2"] == ("Q2", "C1")
    assert result["C1"] == (None, None)


def test_concurrent_lookups_with_resident_readonly_handle(tmp_path: Path, caplog):
    p = _build(tmp_path)
    # Simulate the (now read-only) long-lived stats/federated handle held open.
    resident = EpflGraphStore.open_readonly(p)
    resident.connect()
    try:
        with caplog.at_level(logging.WARNING):
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(
                    pool.map(lambda _: _fetch_category_chain_qids(["C2"], str(p)), range(8)),
                )
        for r in results:
            assert r["C2"] == ("Q2", "C1")
        assert not any(
            "duckdb open failed" in rec.getMessage() for rec in caplog.records
        )
    finally:
        resident.close()
