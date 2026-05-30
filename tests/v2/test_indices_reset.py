"""Unit + integration tests for the index reset / cold-start module.

Covers:
  - Per-provider spec resolution.
  - DuckDB file deletion (idempotent: missing file is OK).
  - Qdrant collection drop with a faked QdrantClient.
  - ProviderCache wipe path.
  - reset_all aggregating per-provider results.
  - The CLI argument parser surface.
  - End-to-end integration: ingest mock records into
    huggingface_models, verify rows in DuckDB, call reset, verify
    the DuckDB file is gone.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from src.v2.indices import reset
from src.v2.indices.reset import (
    ResetResult,
    UnknownProviderError,
    _delete_duckdb,
    _drop_qdrant_collections,
    known_providers,
    reset_all,
    reset_index,
)


# ---------------------------------------------------------------------------
# Spec coverage — every provider in the legacy compact dispatch must be
# resettable too, otherwise an operator who can compact something can't
# wipe it.
# ---------------------------------------------------------------------------


def test_every_known_provider_has_a_resolvable_spec() -> None:
    """`known_providers()` must match what `_load_spec` can resolve;
    no silent gaps where an entry was added to the loader dict but the
    helper function doesn't exist."""
    failures: list[str] = []
    for provider in known_providers():
        try:
            reset._load_spec(provider)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{provider}: {exc!r}")
    assert not failures, f"spec failures: {failures}"


def test_known_providers_covers_compact_dispatch() -> None:
    """Every provider in compact's `_RESOURCE_ATTRS_BY_PROVIDER` must
    also be in reset's known list (the two must stay in sync)."""
    from src.v2.indices.compact import _RESOURCE_ATTRS_BY_PROVIDER

    compact_providers = set(_RESOURCE_ATTRS_BY_PROVIDER)
    reset_providers = set(known_providers())
    missing_from_reset = compact_providers - reset_providers
    assert not missing_from_reset, (
        f"providers in compact but missing from reset: {missing_from_reset}"
    )


# ---------------------------------------------------------------------------
# DuckDB delete
# ---------------------------------------------------------------------------


def test_delete_duckdb_drops_existing_file(tmp_path: Path) -> None:
    db = tmp_path / "x.duckdb"
    db.write_bytes(b"abc" * 100)
    deleted, bytes_freed = _delete_duckdb(db)
    assert deleted is True
    assert bytes_freed == 300
    assert not db.exists()


def test_delete_duckdb_is_idempotent_for_missing_file(tmp_path: Path) -> None:
    db = tmp_path / "nope.duckdb"
    deleted, bytes_freed = _delete_duckdb(db)
    assert deleted is False
    assert bytes_freed == 0


def test_delete_duckdb_also_removes_wal_sidecar(tmp_path: Path) -> None:
    db = tmp_path / "x.duckdb"
    wal = tmp_path / "x.duckdb.wal"
    db.write_bytes(b"a")
    wal.write_bytes(b"b")
    _delete_duckdb(db)
    assert not db.exists()
    assert not wal.exists()


# ---------------------------------------------------------------------------
# Qdrant drop
# ---------------------------------------------------------------------------


class _FakeQdrantClient:
    """In-memory stand-in for `qdrant_client.QdrantClient`."""

    def __init__(self, existing: list[str], *, raise_on_delete: set[str] | None = None) -> None:
        self._existing = set(existing)
        self._raise_on_delete = raise_on_delete or set()
        self.deleted: list[str] = []

    def get_collections(self):  # noqa: ANN201
        return SimpleNamespace(
            collections=[SimpleNamespace(name=n) for n in self._existing],
        )

    def delete_collection(self, *, collection_name: str) -> None:
        if collection_name in self._raise_on_delete:
            raise RuntimeError(f"qdrant disk full: {collection_name}")
        self.deleted.append(collection_name)
        self._existing.discard(collection_name)


def _fake_config(url: str = "http://fake:6333") -> Any:
    return SimpleNamespace(
        qdrant=SimpleNamespace(url=url, api_key=None, prefer_grpc=False),
        paths=SimpleNamespace(cache_db_path=None),
    )


def test_drop_qdrant_collections_skips_absent_names() -> None:
    fake = _FakeQdrantClient(existing=["alpha", "beta"])
    with patch("qdrant_client.QdrantClient", return_value=fake):
        dropped = _drop_qdrant_collections(
            _fake_config(), ["alpha", "gamma"],  # gamma absent
        )
    assert dropped == ["alpha"]
    assert "alpha" not in fake._existing
    assert "beta" in fake._existing  # untouched
    assert "gamma" not in fake.deleted


def test_drop_qdrant_collections_continues_after_individual_failure() -> None:
    """A `delete_collection` exception on one name must not stop the
    drop loop — the reset is best-effort."""
    fake = _FakeQdrantClient(
        existing=["a", "b", "c"],
        raise_on_delete={"b"},
    )
    with patch("qdrant_client.QdrantClient", return_value=fake):
        dropped = _drop_qdrant_collections(_fake_config(), ["a", "b", "c"])
    assert dropped == ["a", "c"]  # b's exception was swallowed


def test_drop_qdrant_collections_returns_empty_on_list_failure() -> None:
    """If `get_collections` itself blows up, drop is skipped (we can't
    know what's there to drop)."""

    class _BrokenClient:
        def get_collections(self):  # noqa: ANN201
            raise RuntimeError("network down")

    with patch("qdrant_client.QdrantClient", return_value=_BrokenClient()):
        dropped = _drop_qdrant_collections(_fake_config(), ["a"])
    assert dropped == []


# ---------------------------------------------------------------------------
# reset_index — full flow with stubs
# ---------------------------------------------------------------------------


def test_reset_index_unknown_provider_raises() -> None:
    with pytest.raises(UnknownProviderError):
        reset_index("not-a-real-provider")


def test_reset_index_huggingface_models_end_to_end(tmp_path, monkeypatch) -> None:
    """Drive reset_index against a real (tmp) huggingface_models
    DuckDB. Stubs out Qdrant; INDEX_DATA_DIR points at tmp."""
    monkeypatch.setenv("INDEX_DATA_DIR", str(tmp_path))

    from src.index.huggingface_models.models import ModelRecord
    from src.index.huggingface_models.paths import get_huggingface_models_paths
    from src.index.huggingface_models.storage.duckdb_store import (
        HuggingFaceModelsStore,
    )

    # 1. Bootstrap a real DuckDB + write a row.
    paths = get_huggingface_models_paths()
    db_path = paths.duckdb_path
    store = HuggingFaceModelsStore.open(db_path)
    store.upsert_model(ModelRecord(repo_id="org/m", downloads=5))
    assert store.count("models") == 1
    store.close()
    assert db_path.exists()
    bytes_before = db_path.stat().st_size

    # 2. Stub out QdrantClient + config so reset doesn't try to reach
    # a real Qdrant. The collection 'huggingface_models' is in the
    # 'existing' list so the drop is exercised.
    fake = _FakeQdrantClient(existing=["huggingface_models", "other"])
    with patch("qdrant_client.QdrantClient", return_value=fake), patch(
        "src.index.huggingface_models.config.load_config",
        return_value=_fake_config(),
    ):
        result = reset_index("huggingface_models", wipe_qdrant=True)

    # 3. DuckDB file should be gone, Qdrant collection dropped.
    assert isinstance(result, ResetResult)
    assert result.provider == "huggingface_models"
    assert result.duckdb_deleted is True
    assert result.duckdb_bytes_reclaimed == bytes_before
    assert result.duckdb_bytes_reclaimed > 0
    assert not db_path.exists()
    assert result.qdrant_collections_dropped == ("huggingface_models",)
    assert "huggingface_models" in fake.deleted
    assert "other" not in fake.deleted  # untouched
    assert result.cache_cleared is False  # wipe_cache defaulted False


def test_reset_index_idempotent_when_already_gone(tmp_path, monkeypatch) -> None:
    """Calling reset twice in a row is fine — second call sees a
    missing DuckDB + missing Qdrant collection and treats both as
    no-ops."""
    monkeypatch.setenv("INDEX_DATA_DIR", str(tmp_path))

    fake = _FakeQdrantClient(existing=[])  # nothing to drop
    with patch("qdrant_client.QdrantClient", return_value=fake), patch(
        "src.index.huggingface_models.config.load_config",
        return_value=_fake_config(),
    ):
        result = reset_index("huggingface_models", wipe_qdrant=True)

    assert result.duckdb_deleted is False  # nothing to delete
    assert result.qdrant_collections_dropped == ()
    assert result.qdrant_skipped is False  # we attempted, just found nothing


def test_reset_index_skips_qdrant_when_flag_off(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INDEX_DATA_DIR", str(tmp_path))

    fake = _FakeQdrantClient(existing=["huggingface_models"])
    with patch("qdrant_client.QdrantClient", return_value=fake), patch(
        "src.index.huggingface_models.config.load_config",
        return_value=_fake_config(),
    ):
        result = reset_index("huggingface_models", wipe_qdrant=False)

    assert result.qdrant_skipped is True
    assert result.qdrant_collections_dropped == ()
    assert fake.deleted == []  # QdrantClient was never even called


def test_reset_index_clears_provider_cache_when_flag_on(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INDEX_DATA_DIR", str(tmp_path))

    cache_path = tmp_path / "providers.db"
    cache_path.write_bytes(b"fake-sqlite")
    config = SimpleNamespace(
        qdrant=SimpleNamespace(url="http://x", api_key=None, prefer_grpc=False),
        paths=SimpleNamespace(cache_db_path=cache_path),
    )
    fake = _FakeQdrantClient(existing=[])
    with patch("qdrant_client.QdrantClient", return_value=fake), patch(
        "src.index.huggingface_models.config.load_config",
        return_value=config,
    ):
        result = reset_index(
            "huggingface_models", wipe_qdrant=True, wipe_cache=True,
        )

    assert result.cache_cleared is True
    assert not cache_path.exists()


# ---------------------------------------------------------------------------
# reset_all
# ---------------------------------------------------------------------------


def test_reset_all_aggregates_per_provider_results(tmp_path, monkeypatch) -> None:
    """Smoke for the bulk path: every provider gets called with no
    DuckDB on disk (so all return duckdb_deleted=False), and the
    aggregated result list has one entry per provider."""
    monkeypatch.setenv("INDEX_DATA_DIR", str(tmp_path))

    fake = _FakeQdrantClient(existing=[])
    # Patch out every config loader so reset_all doesn't try to read
    # real YAML files (which may want credentials).
    with patch("qdrant_client.QdrantClient", return_value=fake), patch.object(
        reset, "_import_config_loader",
        return_value=lambda: _fake_config(),
    ):
        results = reset_all(wipe_qdrant=True)

    provider_names = {r.provider for r in results}
    assert provider_names == set(known_providers())
    assert all(r.duckdb_deleted is False for r in results)


def test_reset_all_continues_when_one_provider_fails(tmp_path, monkeypatch) -> None:
    """A failure on one provider must not stop the others — the bad
    result is just absent from the aggregated list."""
    monkeypatch.setenv("INDEX_DATA_DIR", str(tmp_path))

    def _flaky_loader(dotted: str) -> Any:
        # Make huggingface_models' config loader raise; everything
        # else loads cleanly. The reset of huggingface_models will
        # log a warning and still return a result (the Qdrant drop
        # silently no-ops via the catch in reset_index).
        if "huggingface_models" in dotted:
            raise RuntimeError("yaml missing")
        return lambda: _fake_config()

    fake = _FakeQdrantClient(existing=[])
    with patch("qdrant_client.QdrantClient", return_value=fake), patch.object(
        reset, "_import_config_loader", side_effect=_flaky_loader,
    ):
        results = reset_all(wipe_qdrant=True)

    # huggingface_models is still in the results — its Qdrant drop
    # path catches the exception and continues. Just the
    # qdrant_collections_dropped list is empty for it.
    by_name = {r.provider: r for r in results}
    assert "huggingface_models" in by_name
    assert by_name["huggingface_models"].qdrant_collections_dropped == ()
    # And the rest still ran.
    assert len(results) == len(known_providers())


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def test_cli_argparse_rejects_unknown_provider(capsys) -> None:
    """The CLI should fail cleanly when given a typo'd provider."""
    import sys

    with patch.object(sys, "argv", ["reset", "huggingface_modls"]):  # typo
        with pytest.raises(SystemExit) as exc_info:
            reset._cli_main()
    assert exc_info.value.code != 0
