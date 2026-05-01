from __future__ import annotations

import pytest

from src.index.ror import paths as ror_paths
from src.index.ror.models import IndexedRecord, IndexManifest
from src.index.ror.store import (
    now_iso,
    read_manifest,
    read_records,
    write_sidecar,
)


@pytest.fixture
def isolated_index_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("INDEX_DATA_DIR", str(tmp_path))
    yield tmp_path


def _make_rows(n: int):
    return [
        IndexedRecord(
            row=i,
            ror_id=f"https://ror.org/test{i:04d}",
            name=f"Test Org {i}",
            text=f"Name: Test Org {i}",
            record={"id": f"https://ror.org/test{i:04d}"},
        )
        for i in range(n)
    ]


def _make_manifest(n: int) -> IndexManifest:
    return IndexManifest(
        scope_mode="epfl_ethz",
        record_count=n,
        embedding_model="Qwen/Qwen3-Embedding-8B",
        embedding_dim=8,
        reranker_model="Qwen/Qwen3-Reranker-8B",
        ror_release_version="test-1",
        ror_release_doi=None,
        built_at_iso=now_iso(),
    )


def test_round_trip_sidecar_records_and_manifest(isolated_index_dir):
    n = 5
    rows = _make_rows(n)
    write_sidecar("epfl_ethz", rows, _make_manifest(n))

    read_back = read_records("epfl_ethz")
    assert [r.row for r in read_back] == list(range(n))
    assert [r.ror_id for r in read_back] == [r.ror_id for r in rows]

    manifest = read_manifest("epfl_ethz")
    assert manifest.record_count == n
    assert manifest.embedding_dim == 8


def test_paths_respect_env_var(isolated_index_dir):
    expected_root = isolated_index_dir / "ror"
    assert ror_paths.ror_data_dir() == expected_root
    assert ror_paths.dump_dir() == expected_root / "dump"
    assert ror_paths.index_dir("switzerland") == expected_root / "index" / "switzerland"
