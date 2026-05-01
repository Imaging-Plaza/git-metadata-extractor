"""Shared fixtures for the HuggingFace index tests."""

from __future__ import annotations

import pytest

from src.index.huggingface.config import HuggingFaceIndexConfig, load_config
from src.index.huggingface.storage.duckdb_store import DuckDBStore


@pytest.fixture()
def tmp_store(tmp_path) -> DuckDBStore:
    db_path = tmp_path / "huggingface.duckdb"
    store = DuckDBStore(db_path)
    store.bootstrap()
    yield store
    store.close()


@pytest.fixture()
def base_config(monkeypatch, tmp_path) -> HuggingFaceIndexConfig:
    """Config loaded from the real YAML, with required envs populated and
    the data dir redirected at a tmp path so tests don't touch real state."""
    monkeypatch.setenv("INDEX_DATA_DIR", str(tmp_path / "index_data"))
    monkeypatch.setenv("RCP_TOKEN", "test-token")
    monkeypatch.setenv("HF_TOKEN", "hf-test-token")
    return load_config()
