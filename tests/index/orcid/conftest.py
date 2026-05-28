"""Shared fixtures for the ORCID index tests."""

from __future__ import annotations

import pytest

from src.index.orcid.config import OrcidIndexConfig, load_config
from src.index.orcid.storage.duckdb_store import OrcidDuckDBStore


@pytest.fixture()
def tmp_store(tmp_path) -> OrcidDuckDBStore:
    """Fresh DuckDB store rooted in a tmp_path-isolated file."""
    db_path = tmp_path / "orcid.duckdb"
    store = OrcidDuckDBStore(db_path)
    store.bootstrap()
    yield store
    store.close()


@pytest.fixture()
def base_config(monkeypatch, tmp_path) -> OrcidIndexConfig:
    """Config loaded from the real YAML, with non-empty required envs and
    paths redirected into tmp_path."""
    monkeypatch.setenv("INDEX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RCP_TOKEN", "test-token")
    return load_config(scope="epfl")
