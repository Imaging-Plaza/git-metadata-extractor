"""Tests for `src/index/ror/storage/migrate_storage.py` (D16, PR 3).

Synthetic tmp-path fixtures only — no live Qdrant, no live RCP, no Zenodo.
Validates: dump-discovery, full-dump load, per-scope JSONL→DuckDB porting,
manifest round-trip, and the `--skip-qdrant-check` path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.index.ror.storage import migrate_storage
from src.index.ror.storage.duckdb_store import DuckDBStore, vector_id_for


# ---------------------------------------------------------------------------
# Tmp-path scaffolding mimicking the on-disk layout the migrator expects.
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_index_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("INDEX_DATA_DIR", str(tmp_path))
    return tmp_path


def _seed_dump_json(index_root: Path, version: str = "v2.6") -> Path:
    """Write a tiny ROR dump JSON under <root>/ror/dump/<version>/."""
    vdir = index_root / "ror" / "dump" / version
    vdir.mkdir(parents=True, exist_ok=True)
    json_path = vdir / f"{version}-2026-04-14-ror-data.json"
    json_path.write_text(
        json.dumps([
            {
                "id": "https://ror.org/02s376052",
                "names": [
                    {"value": "École polytechnique fédérale de Lausanne", "types": ["ror_display", "label"]},
                    {"value": "EPFL", "types": ["acronym"]},
                ],
                "status": "active",
                "types": ["education"],
                "locations": [{"geonames_details": {"country_code": "CH", "name": "Lausanne"}}],
            },
            {
                "id": "https://ror.org/05a28rw58",
                "names": [{"value": "ETH Zurich", "types": ["ror_display"]}],
                "status": "active",
                "types": ["education"],
                "locations": [{"geonames_details": {"country_code": "CH", "name": "Zürich"}}],
            },
            {
                "id": "https://ror.org/abandoned1",
                "names": [{"value": "Defunct Lab", "types": ["ror_display"]}],
                "status": "withdrawn",
                "locations": [{"geonames_details": {"country_code": "FR"}}],
            },
        ]),
        encoding="utf-8",
    )
    (vdir / "release.json").write_text(
        json.dumps({"version": version, "doi": "10.5281/zenodo.19576723"}),
        encoding="utf-8",
    )
    return json_path


def _seed_scope_sidecar(
    index_root: Path,
    scope_mode: str,
    ror_ids: list[str],
    *,
    embedding_dim: int = 4096,
) -> None:
    sdir = index_root / "ror" / "index" / scope_mode
    sdir.mkdir(parents=True, exist_ok=True)
    with (sdir / "records.jsonl").open("w", encoding="utf-8") as f:
        for i, rid in enumerate(ror_ids):
            f.write(json.dumps({
                "row": i,
                "ror_id": rid,
                "name": rid.rsplit("/", 1)[-1],
                "text": f"Name: {rid.rsplit('/', 1)[-1]}",
                "record": {"id": rid},
            }) + "\n")
    (sdir / "manifest.json").write_text(
        json.dumps({
            "scope_mode": scope_mode,
            "record_count": len(ror_ids),
            "embedding_model": "Qwen/Qwen3-Embedding-8B",
            "embedding_dim": embedding_dim,
            "reranker_model": "Qwen/Qwen3-Reranker-8B",
            "ror_release_version": "v2.6",
            "ror_release_doi": "10.5281/zenodo.19576723",
            "built_at_iso": "2026-05-01T07:04:45.642340+00:00",
        }, indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_find_cached_dump_json_picks_latest_version(isolated_index_dir):
    _seed_dump_json(isolated_index_dir, version="v2.5")
    latest = _seed_dump_json(isolated_index_dir, version="v2.6")
    found = migrate_storage.find_cached_dump_json()
    assert found == latest


def test_find_cached_dump_json_returns_none_when_no_dump(isolated_index_dir):
    assert migrate_storage.find_cached_dump_json() is None


def test_list_scope_dirs_sorted(isolated_index_dir):
    for s in ("worldwide", "epfl_ethz", "switzerland", "europe"):
        (isolated_index_dir / "ror" / "index" / s).mkdir(parents=True)
    assert migrate_storage.list_scope_dirs() == [
        "epfl_ethz", "europe", "switzerland", "worldwide",
    ]


# ---------------------------------------------------------------------------
# Full-dump and per-scope loaders
# ---------------------------------------------------------------------------


def test_populate_full_dump_writes_records_table(isolated_index_dir):
    json_path = _seed_dump_json(isolated_index_dir)
    store = DuckDBStore.open()
    n = migrate_storage.populate_full_dump(store, json_path, release_version="v2.6")
    assert n == 3
    assert store.count_records() == 3
    epfl = store.fetch_record("02s376052")
    assert epfl is not None
    assert epfl["country_code"] == "CH"
    assert epfl["ror_release_version"] == "v2.6"
    store.close()


def test_populate_scope_replaces_rows_and_writes_manifest(isolated_index_dir):
    _seed_dump_json(isolated_index_dir)
    _seed_scope_sidecar(isolated_index_dir, "epfl_ethz", [
        "https://ror.org/02s376052",
        "https://ror.org/05a28rw58",
    ])
    store = DuckDBStore.open()
    summary = migrate_storage.populate_scope(store, "epfl_ethz")
    assert summary["rows"] == 2
    assert store.count_scope_records("epfl_ethz") == 2

    manifest = store.fetch_manifest("epfl_ethz")
    assert manifest is not None
    assert manifest["embedding_dim"] == 4096
    assert manifest["ror_release_version"] == "v2.6"

    # vector_id is the deterministic UUIDv5 — matches what Qdrant has for
    # the same point id.
    cur = store.connect().execute(
        "SELECT vector_id FROM scope_records WHERE ror_id = ?",
        ["https://ror.org/02s376052"],
    )
    assert cur.fetchone()[0] == vector_id_for("https://ror.org/02s376052")
    store.close()


def test_populate_scope_raises_when_sidecar_missing(isolated_index_dir):
    store = DuckDBStore.open()
    with pytest.raises(FileNotFoundError):
        migrate_storage.populate_scope(store, "ghost_scope")
    store.close()


# ---------------------------------------------------------------------------
# End-to-end migrate_all
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_cfg() -> Any:
    """Minimal cfg stub. `migrate_all` only needs it to construct QdrantRorStore,
    and we mock that out below."""
    return MagicMock(name="RorIndexConfig")


def test_migrate_all_loads_dump_and_scopes(isolated_index_dir, fake_cfg):
    _seed_dump_json(isolated_index_dir)
    _seed_scope_sidecar(isolated_index_dir, "epfl_ethz", [
        "https://ror.org/02s376052",
    ])
    _seed_scope_sidecar(isolated_index_dir, "switzerland", [
        "https://ror.org/02s376052",
        "https://ror.org/05a28rw58",
    ])

    fake_qstore = MagicMock()
    fake_qstore.count.side_effect = lambda scope: {"epfl_ethz": 1, "switzerland": 2}[scope]

    with patch("src.index.ror.storage.migrate_storage.QdrantRorStore", return_value=fake_qstore):
        summary = migrate_storage.migrate_all(fake_cfg)

    assert summary["records_loaded"] == 3
    assert summary["release_version"] == "v2.6"
    scope_modes = {s["scope_mode"] for s in summary["scopes"]}
    assert scope_modes == {"epfl_ethz", "switzerland"}
    checks = {c["scope_mode"]: c for c in summary["qdrant_checks"]}
    assert checks["epfl_ethz"]["match"] is True
    assert checks["switzerland"]["match"] is True


def test_migrate_all_skip_qdrant_check_omits_qdrant_calls(isolated_index_dir, fake_cfg):
    _seed_dump_json(isolated_index_dir)
    _seed_scope_sidecar(isolated_index_dir, "epfl_ethz", ["https://ror.org/02s376052"])

    with patch("src.index.ror.storage.migrate_storage.QdrantRorStore") as mock_q:
        summary = migrate_storage.migrate_all(fake_cfg, skip_qdrant_check=True)

    mock_q.assert_not_called()
    assert summary["qdrant_checks"] == []
    assert len(summary["scopes"]) == 1


def test_migrate_all_raises_when_dump_missing(isolated_index_dir, fake_cfg):
    # No dump seeded.
    _seed_scope_sidecar(isolated_index_dir, "epfl_ethz", ["https://ror.org/02s376052"])
    with pytest.raises(FileNotFoundError, match="No cached ROR dump"):
        migrate_storage.migrate_all(fake_cfg, skip_qdrant_check=True)


def test_migrate_all_continues_when_qdrant_unreachable(isolated_index_dir, fake_cfg):
    """If Qdrant is down, the DuckDB migration still succeeds and the check
    section reports a None count (not an exception)."""
    _seed_dump_json(isolated_index_dir)
    _seed_scope_sidecar(isolated_index_dir, "epfl_ethz", ["https://ror.org/02s376052"])

    fake_qstore = MagicMock()
    fake_qstore.count.side_effect = ConnectionError("Qdrant down")

    with patch("src.index.ror.storage.migrate_storage.QdrantRorStore", return_value=fake_qstore):
        summary = migrate_storage.migrate_all(fake_cfg)

    assert summary["records_loaded"] == 3
    assert summary["qdrant_checks"][0]["qdrant_count"] is None
    assert summary["qdrant_checks"][0]["match"] is False
