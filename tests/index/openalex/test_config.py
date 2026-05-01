"""Config loader: YAML defaults + env-injected secrets + env overrides."""

from __future__ import annotations

import pytest

from src.index.openalex.config import (
    MISSING_MAILTO_ERROR,
    MISSING_RCP_TOKEN_ERROR,
    load_config,
)


@pytest.mark.openalex()
def test_defaults_from_yaml(monkeypatch):
    monkeypatch.delenv("OPENALEX_MAILTO", raising=False)
    monkeypatch.delenv("RCP_TOKEN", raising=False)
    cfg = load_config()
    assert cfg.rcp.base_url == "https://inference-rcp.epfl.ch/v1"
    assert cfg.rcp.embedding_model == "Qwen/Qwen3-Embedding-8B"
    assert cfg.rcp.embedding_dim == 4096
    assert cfg.rcp.reranker_model == "Qwen/Qwen3-Reranker-8B"
    assert cfg.scope.ror == "https://ror.org/02s376052"
    assert cfg.scope.country == "ch"
    assert cfg.openalex.base_url == "https://api.openalex.org"
    assert cfg.openalex.per_page == 200
    assert cfg.qdrant.url == "http://localhost:6333"
    assert cfg.chunking.size_tokens == 256
    assert cfg.chunking.overlap_tokens == 64
    # No env tokens were set.
    assert cfg.openalex.mailto is None
    assert cfg.rcp.token is None


@pytest.mark.openalex()
def test_env_secrets_inject(monkeypatch):
    monkeypatch.setenv("OPENALEX_MAILTO", "me@x.com")
    monkeypatch.setenv("RCP_TOKEN", "secret")
    monkeypatch.setenv("INDEX_QDRANT_API_KEY", "qkey")
    cfg = load_config()
    assert cfg.openalex.mailto == "me@x.com"
    assert cfg.rcp.token == "secret"
    assert cfg.qdrant.api_key == "qkey"


@pytest.mark.openalex()
def test_env_overrides_apply(monkeypatch):
    monkeypatch.setenv("INDEX_QDRANT_URL", "http://qdrant.test:6333")
    monkeypatch.setenv("INDEX_QDRANT_PREFER_GRPC", "true")
    monkeypatch.setenv("INDEX_OPENALEX_SCOPE_ROR", "https://ror.org/0XXXX")
    monkeypatch.setenv("INDEX_OPENALEX_SCOPE_COUNTRY", "de")
    cfg = load_config()
    assert cfg.qdrant.url == "http://qdrant.test:6333"
    assert cfg.qdrant.prefer_grpc is True
    assert cfg.scope.ror == "https://ror.org/0XXXX"
    assert cfg.scope.country == "de"


@pytest.mark.openalex()
def test_require_ingest_blocks_when_mailto_missing(monkeypatch):
    monkeypatch.delenv("OPENALEX_MAILTO", raising=False)
    cfg = load_config()
    with pytest.raises(ValueError) as exc:
        cfg.require_ingest()
    assert MISSING_MAILTO_ERROR in str(exc.value)


@pytest.mark.openalex()
def test_require_rcp_blocks_when_token_missing(monkeypatch):
    monkeypatch.delenv("RCP_TOKEN", raising=False)
    cfg = load_config()
    with pytest.raises(ValueError) as exc:
        cfg.require_rcp()
    assert MISSING_RCP_TOKEN_ERROR in str(exc.value)


@pytest.mark.openalex()
def test_paths_attached_to_config(monkeypatch, tmp_path):
    monkeypatch.setenv("INDEX_DATA_DIR", str(tmp_path))
    cfg = load_config()
    assert cfg.paths.root == tmp_path / "openalex"
