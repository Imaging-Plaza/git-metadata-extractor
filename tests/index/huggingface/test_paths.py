"""Path resolution: env override + default fallback + cards layout."""

from __future__ import annotations

from src.index.huggingface.paths import get_huggingface_paths


def test_default_path_under_repo_root(monkeypatch):
    monkeypatch.delenv("INDEX_DATA_DIR", raising=False)
    paths = get_huggingface_paths()
    assert paths.root.name == "huggingface"
    assert paths.duckdb_path.name == "huggingface.duckdb"
    assert paths.duckdb_dir.exists()
    assert paths.cards_dir.exists()


def test_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("INDEX_DATA_DIR", str(tmp_path / "myindex"))
    paths = get_huggingface_paths()
    assert paths.root == tmp_path / "myindex" / "huggingface"
    assert paths.duckdb_dir.exists()
    assert paths.cards_dir.exists()
    assert paths.logs_dir.exists()


def test_cards_path_for(monkeypatch, tmp_path):
    monkeypatch.setenv("INDEX_DATA_DIR", str(tmp_path / "myindex"))
    paths = get_huggingface_paths()
    target = paths.cards_path_for("models", "epfl-llm/meditron-7b")
    assert target == paths.cards_dir / "models" / "epfl-llm/meditron-7b"
