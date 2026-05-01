"""Path resolution: env override + default fallback."""

from __future__ import annotations

import pytest

from src.index.openalex.paths import get_openalex_paths


@pytest.mark.openalex()
def test_default_path_under_repo_root(monkeypatch):
    monkeypatch.delenv("INDEX_DATA_DIR", raising=False)
    paths = get_openalex_paths()
    assert paths.root.name == "openalex"
    assert paths.duckdb_path.name == "openalex.duckdb"
    assert paths.duckdb_dir.exists()


@pytest.mark.openalex()
def test_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("INDEX_DATA_DIR", str(tmp_path / "myindex"))
    paths = get_openalex_paths()
    assert paths.root == tmp_path / "myindex" / "openalex"
    assert paths.duckdb_dir.exists()
    assert paths.cache_dir.exists()
    assert paths.logs_dir.exists()


@pytest.mark.openalex()
def test_relative_env_resolves_against_repo(monkeypatch):
    monkeypatch.setenv("INDEX_DATA_DIR", "data/index_test")
    paths = get_openalex_paths()
    assert paths.root.is_absolute()
    assert paths.root.name == "openalex"
