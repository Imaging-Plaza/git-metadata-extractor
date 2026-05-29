"""Tests for the re-implemented `huggingface_models` lineage walker.

Drives `compute_lineage` against a real tmp DuckDB with a synthetic
parent/child graph. Verifies:
  - ancestor walk follows `base_models` upward
  - descendant walk finds rows whose `base_models` contains the parent
  - depth caps the walk
  - empty levels are dropped
  - cycles don't infinite-loop (seen-set guard)
  - thin payload (no `raw` / `card_data` leak)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.index.huggingface_models.models import ModelRecord
from src.index.huggingface_models.retrieval.lineage import (
    _coerce_repo_id_list,
    _thin_record,
    compute_lineage,
)
from src.index.huggingface_models.storage.duckdb_store import (
    HuggingFaceModelsStore,
)


@pytest.fixture()
def models_store(tmp_path: Path) -> HuggingFaceModelsStore:
    store = HuggingFaceModelsStore.open(tmp_path / "huggingface_models.duckdb")
    yield store
    store.close()


def _seed(
    store: HuggingFaceModelsStore,
    repo_id: str,
    *,
    base_models: list[str] | None = None,
    downloads: int = 0,
) -> None:
    store.upsert_model(
        ModelRecord(
            repo_id=repo_id,
            base_models=base_models or [],
            downloads=downloads,
        ),
    )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_coerce_repo_id_list_handles_python_list() -> None:
    assert _coerce_repo_id_list(["a", "b"]) == ["a", "b"]


def test_coerce_repo_id_list_handles_json_string() -> None:
    assert _coerce_repo_id_list('["a", "b"]') == ["a", "b"]


def test_coerce_repo_id_list_handles_garbage() -> None:
    assert _coerce_repo_id_list(None) == []
    assert _coerce_repo_id_list("not-json") == []
    assert _coerce_repo_id_list(42) == []
    assert _coerce_repo_id_list('{"not": "a list"}') == []


def test_thin_record_drops_heavy_fields() -> None:
    row = {
        "repo_id": "o/m",
        "author": "o",
        "pipeline_tag": "x",
        "library_name": "transformers",
        "license": "mit",
        "downloads": 100,
        "likes": 5,
        "last_modified": None,
        "raw": {"big": "blob"},
        "card_data": {"more": "blob"},
        "base_models": ["o/parent"],
    }
    thin = _thin_record(row)
    assert "raw" not in thin
    assert "card_data" not in thin
    assert "base_models" not in thin
    assert thin["repo_id"] == "o/m"
    assert thin["downloads"] == 100


# ---------------------------------------------------------------------------
# Ancestor walk
# ---------------------------------------------------------------------------


def test_lineage_walks_ancestors_one_level(models_store: HuggingFaceModelsStore) -> None:
    """child → parent"""
    _seed(models_store, "org/parent")
    _seed(models_store, "org/child", base_models=["org/parent"])

    out = compute_lineage("org/child", store=models_store, depth=3)

    assert out["root"] == "org/child"
    assert "level_1" in out["ancestors"]
    parent_ids = [r["repo_id"] for r in out["ancestors"]["level_1"]]
    assert parent_ids == ["org/parent"]
    assert {"from": "org/child", "to": "org/parent"} in out["edges"]


def test_lineage_walks_ancestors_two_levels(models_store: HuggingFaceModelsStore) -> None:
    """grandchild → child → parent"""
    _seed(models_store, "org/parent")
    _seed(models_store, "org/child", base_models=["org/parent"])
    _seed(models_store, "org/grandchild", base_models=["org/child"])

    out = compute_lineage("org/grandchild", store=models_store, depth=3)

    l1 = [r["repo_id"] for r in out["ancestors"]["level_1"]]
    l2 = [r["repo_id"] for r in out["ancestors"]["level_2"]]
    assert l1 == ["org/child"]
    assert l2 == ["org/parent"]


def test_lineage_emits_synthetic_row_for_unknown_parent(
    models_store: HuggingFaceModelsStore,
) -> None:
    """If a `base_models` parent isn't ingested locally, we still emit
    an ancestor entry carrying just the `repo_id`. Lineage operators
    can then choose whether to fetch the parent from HF on demand."""
    _seed(models_store, "org/child", base_models=["external/unknown-parent"])

    out = compute_lineage("org/child", store=models_store, depth=3)

    l1 = out["ancestors"]["level_1"]
    assert len(l1) == 1
    assert l1[0]["repo_id"] == "external/unknown-parent"
    # No other fields are populated for the synthetic stub.
    assert l1[0].get("downloads") is None
    assert l1[0].get("license") is None


# ---------------------------------------------------------------------------
# Descendant walk
# ---------------------------------------------------------------------------


def test_lineage_walks_descendants_one_level(
    models_store: HuggingFaceModelsStore,
) -> None:
    """Two children fine-tuned from one parent — both descendants are
    found via the JSON-containment scan."""
    _seed(models_store, "org/parent")
    _seed(models_store, "org/child1", base_models=["org/parent"], downloads=100)
    _seed(models_store, "org/child2", base_models=["org/parent"], downloads=50)

    out = compute_lineage("org/parent", store=models_store, depth=3)

    descs = [r["repo_id"] for r in out["descendants"]["level_1"]]
    # Ordered by downloads DESC NULLS LAST.
    assert descs == ["org/child1", "org/child2"]
    # Edges point child → parent (from=child, to=parent).
    assert {"from": "org/child1", "to": "org/parent"} in out["edges"]
    assert {"from": "org/child2", "to": "org/parent"} in out["edges"]


def test_lineage_walks_descendants_across_levels(
    models_store: HuggingFaceModelsStore,
) -> None:
    """parent → child → grandchild"""
    _seed(models_store, "org/parent")
    _seed(models_store, "org/child", base_models=["org/parent"])
    _seed(models_store, "org/grandchild", base_models=["org/child"])

    out = compute_lineage("org/parent", store=models_store, depth=3)

    l1 = [r["repo_id"] for r in out["descendants"]["level_1"]]
    l2 = [r["repo_id"] for r in out["descendants"]["level_2"]]
    assert l1 == ["org/child"]
    assert l2 == ["org/grandchild"]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_lineage_respects_depth_limit(models_store: HuggingFaceModelsStore) -> None:
    """A 4-deep chain with depth=2 should stop at level 2."""
    _seed(models_store, "org/a")
    _seed(models_store, "org/b", base_models=["org/a"])
    _seed(models_store, "org/c", base_models=["org/b"])
    _seed(models_store, "org/d", base_models=["org/c"])

    out = compute_lineage("org/d", store=models_store, depth=2)

    # Only levels 1 and 2 appear; level_3 / level_4 are absent
    # (empty levels are dropped).
    assert set(out["ancestors"].keys()) == {"level_1", "level_2"}
    l1 = [r["repo_id"] for r in out["ancestors"]["level_1"]]
    l2 = [r["repo_id"] for r in out["ancestors"]["level_2"]]
    assert l1 == ["org/c"]
    assert l2 == ["org/b"]


def test_lineage_empty_for_isolated_node(
    models_store: HuggingFaceModelsStore,
) -> None:
    """A model with no parents and no children → empty ancestors +
    descendants dicts (no level_* keys)."""
    _seed(models_store, "org/isolated")

    out = compute_lineage("org/isolated", store=models_store, depth=3)
    assert out["ancestors"] == {}
    assert out["descendants"] == {}
    assert out["edges"] == []


def test_lineage_does_not_loop_on_cycle(
    models_store: HuggingFaceModelsStore,
) -> None:
    """If a → b → a (impossible in practice, but defensible), the
    seen-set guard prevents infinite recursion. The walk just
    terminates at depth limit with each node visited at most once."""
    _seed(models_store, "org/a", base_models=["org/b"])
    _seed(models_store, "org/b", base_models=["org/a"])

    out = compute_lineage("org/a", store=models_store, depth=10)

    # Should terminate (test would hang otherwise) — and visit each
    # node at most once.
    visited = [r["repo_id"] for level in out["ancestors"].values() for r in level]
    assert len(set(visited)) == len(visited)


def test_lineage_returns_depth_in_output(
    models_store: HuggingFaceModelsStore,
) -> None:
    out = compute_lineage("org/x", store=models_store, depth=7)
    assert out["depth"] == 7
