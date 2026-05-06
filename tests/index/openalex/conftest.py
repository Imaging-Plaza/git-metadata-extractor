"""Shared fixtures for the OpenAlex index tests."""

from __future__ import annotations

import pytest

from src.index.openalex.config import OpenAlexIndexConfig, load_config
from src.index.openalex.storage.duckdb_store import DuckDBStore


@pytest.fixture()
def tmp_store(tmp_path) -> DuckDBStore:
    """Fresh DuckDB store rooted in a tmp_path-isolated file."""
    db_path = tmp_path / "openalex.duckdb"
    store = DuckDBStore(db_path)
    store.bootstrap()
    yield store
    store.close()


@pytest.fixture()
def base_config(monkeypatch) -> OpenAlexIndexConfig:
    """Config loaded from the real YAML, with non-empty required envs."""
    monkeypatch.setenv("OPENALEX_MAILTO", "tester@example.com")
    monkeypatch.setenv("RCP_TOKEN", "test-token")
    return load_config()
