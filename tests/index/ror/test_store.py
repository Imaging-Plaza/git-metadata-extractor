"""Legacy sidecar reader tests.

After D16, `store.py` is read-only legacy support. These tests ensure
`read_records` / `read_manifest` still parse pre-D16 sidecars (so the
`migrate-storage` porter and the D15 FAISS→Qdrant migrator keep working).
"""

from __future__ import annotations

import json

import pytest

from src.index.ror import paths as ror_paths
from src.index.ror.models import IndexedRecord, IndexManifest
from src.index.ror.store import now_iso, read_manifest, read_records


@pytest.fixture
def isolated_index_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("INDEX_DATA_DIR", str(tmp_path))
    yield tmp_path


def _seed_legacy_sidecar(scope_mode: str, n: int) -> None:
    sdir = ror_paths.index_dir(scope_mode)
    rp = sdir / "records.jsonl"
    mp = sdir / "manifest.json"
    rows = [
        IndexedRecord(
            row=i,
            ror_id=f"https://ror.org/test{i:04d}",
            name=f"Test Org {i}",
            text=f"Name: Test Org {i}",
            record={"id": f"https://ror.org/test{i:04d}"},
        )
        for i in range(n)
    ]
    with rp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row.model_dump(), ensure_ascii=False) + "\n")
    manifest = IndexManifest(
        scope_mode=scope_mode,
        record_count=n,
        embedding_model="Qwen/Qwen3-Embedding-8B",
        embedding_dim=8,
        reranker_model="Qwen/Qwen3-Reranker-8B",
        ror_release_version="test-1",
        ror_release_doi=None,
        built_at_iso=now_iso(),
    )
    mp.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")


def test_read_records_parses_legacy_sidecar(isolated_index_dir):
    _seed_legacy_sidecar("epfl_ethz", 5)
    read_back = read_records("epfl_ethz")
    assert [r.row for r in read_back] == list(range(5))
    assert read_back[0].ror_id == "https://ror.org/test0000"


def test_read_manifest_parses_legacy_sidecar(isolated_index_dir):
    _seed_legacy_sidecar("switzerland", 3)
    manifest = read_manifest("switzerland")
    assert manifest.record_count == 3
    assert manifest.embedding_dim == 8
    assert manifest.scope_mode == "switzerland"


def test_read_records_raises_when_sidecar_missing(isolated_index_dir):
    with pytest.raises(FileNotFoundError):
        read_records("ghost_scope")


def test_paths_respect_env_var(isolated_index_dir):
    expected_root = isolated_index_dir / "ror"
    assert ror_paths.ror_data_dir() == expected_root
    assert ror_paths.dump_dir() == expected_root / "dump"
    assert ror_paths.index_dir("switzerland") == expected_root / "index" / "switzerland"
