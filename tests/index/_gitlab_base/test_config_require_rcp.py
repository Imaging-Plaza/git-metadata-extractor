# tests/index/_gitlab_base/test_config_require_rcp.py
"""Bug 10: GitLabIndexConfig.require_rcp() must exist (it is called
unconditionally by RCPEmbeddingClient.__init__) and turn a missing RCP_TOKEN
into a clear ValueError instead of an AttributeError that blocked every embed.
"""
from __future__ import annotations

import pytest

from src.index._gitlab_base.config_base import (
    MISSING_RCP_TOKEN_ERROR,
    GitLabIndexConfig,
    RcpConfig,
)


def _rcp(token: str | None) -> RcpConfig:
    return RcpConfig(
        base_url="http://rcp",
        embedding_model="m",
        embedding_dim=8,
        query_instruction="q",
        reranker_model="r",
        token=token,
    )


def test_require_rcp_raises_without_token():
    cfg = GitLabIndexConfig.model_construct(rcp=_rcp(None))
    with pytest.raises(ValueError, match="RCP_TOKEN"):
        cfg.require_rcp()


def test_require_rcp_passes_with_token():
    cfg = GitLabIndexConfig.model_construct(rcp=_rcp("secret"))
    assert cfg.require_rcp() is None


def test_error_constant_mentions_rcp_token():
    assert "RCP_TOKEN" in MISSING_RCP_TOKEN_ERROR
