"""Config loading + scope/seed lookups."""

from __future__ import annotations

import pytest


def test_load_config_picks_up_envs(base_config):
    assert base_config.rcp.token == "test-token"
    assert base_config.huggingface.token == "hf-test-token"
    assert base_config.scope.active in {"epfl", "switzerland"}


def test_seed_for_known_scope(base_config):
    epfl_seed = base_config.seed_for("epfl")
    swiss_seed = base_config.seed_for("switzerland")
    assert "epfl-llm" in epfl_seed
    assert "EPFL-VILAB" in epfl_seed
    # Switzerland seed should be a superset of EPFL seed.
    assert set(epfl_seed).issubset(set(swiss_seed))
    assert "swiss-ai" in swiss_seed
    assert "ZurichNLP" in swiss_seed


def test_seed_for_unknown_scope_raises(base_config):
    with pytest.raises(ValueError, match="Unknown scope"):
        base_config.seed_for("germany")


def test_search_terms_for_scope(base_config):
    assert "epfl" in base_config.search_terms_for("epfl")
    assert "swiss" in base_config.search_terms_for("switzerland")


def test_active_scope_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("INDEX_DATA_DIR", str(tmp_path / "x"))
    monkeypatch.setenv("RCP_TOKEN", "t")
    monkeypatch.setenv("INDEX_HUGGINGFACE_SCOPE", "switzerland")
    from src.index.huggingface.config import load_config

    cfg = load_config()
    assert cfg.scope.active == "switzerland"


def test_require_rcp(monkeypatch, tmp_path):
    monkeypatch.setenv("INDEX_DATA_DIR", str(tmp_path / "x"))
    monkeypatch.delenv("RCP_TOKEN", raising=False)
    from src.index.huggingface.config import load_config

    cfg = load_config()
    with pytest.raises(ValueError, match="RCP_TOKEN"):
        cfg.require_rcp()
