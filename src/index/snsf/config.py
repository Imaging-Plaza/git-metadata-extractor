"""Pydantic config for the SNSF P3 index.

Resolved by `load_config()` from `config/index/snsf.yaml` and overlayed
with env vars: `RCP_TOKEN`, `INDEX_QDRANT_URL`, `INDEX_QDRANT_API_KEY`,
`INDEX_QDRANT_PREFER_GRPC`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import yaml
from pydantic import BaseModel, Field

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "index" / "snsf.yaml"


ScopeMode = Literal["epfl", "ethz", "eth_domain", "switzerland"]


class SnsfApiConfig(BaseModel):
    base_url: str = "https://data.snf.ch"
    request_timeout_s: float = 60.0
    page_size: int = 1000
    inter_request_sleep_s: float = 0.2
    user_agent: str = (
        "git-metadata-extractor/snsf (polite scraper; email if issues)"
    )


class ScopeConfig(BaseModel):
    active: ScopeMode = "epfl"
    filters: Dict[str, Dict[str, List[str]]] = Field(default_factory=dict)

    def filter_for_active_scope(self) -> Dict[str, List[str]]:
        return dict(self.filters.get(self.active, {}))


class RcpConfig(BaseModel):
    """EPFL RCP OpenAI-compatible inference gateway."""

    base_url: str = "https://inference-rcp.epfl.ch/v1"
    embedding_model: str = "Qwen/Qwen3-Embedding-8B"
    embedding_dim: int = 4096
    query_instruction: str = (
        "Given a query, retrieve SNSF grant titles and abstracts that match"
    )
    reranker_model: str = "Qwen/Qwen3-Reranker-8B"
    batch_size: int = 32
    max_concurrency: int = 4
    timeout_seconds: int = 120
    token: Optional[str] = None


class QdrantConfig(BaseModel):
    url: str = "http://gme-qdrant:6333"
    api_key: Optional[str] = None
    prefer_grpc: bool = False
    collection_prefix: str = "snsf"


class RetrievalConfig(BaseModel):
    top_k: int = 50
    rerank_top_k: int = 10


class SnsfIndexConfig(BaseModel):
    snsf: SnsfApiConfig = Field(default_factory=SnsfApiConfig)
    scope: ScopeConfig = Field(default_factory=ScopeConfig)
    rcp: RcpConfig = Field(default_factory=RcpConfig)
    qdrant: QdrantConfig = Field(default_factory=QdrantConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)

    def collection_name(self) -> str:
        return f"{self.qdrant.collection_prefix}_{self.scope.active}"


def _env_override(cfg: SnsfIndexConfig) -> SnsfIndexConfig:
    """Apply env-var overrides post-load. Same shape as the ror sibling."""
    rcp_token = os.environ.get("RCP_TOKEN", "").strip()
    if rcp_token:
        cfg.rcp.token = rcp_token

    qurl = os.environ.get("INDEX_QDRANT_URL", "").strip()
    if qurl:
        cfg.qdrant.url = qurl
    qkey = os.environ.get("INDEX_QDRANT_API_KEY", "").strip()
    if qkey:
        cfg.qdrant.api_key = qkey
    qgrpc = os.environ.get("INDEX_QDRANT_PREFER_GRPC", "").strip().lower()
    if qgrpc in {"1", "true", "yes", "on"}:
        cfg.qdrant.prefer_grpc = True
    elif qgrpc in {"0", "false", "no", "off"}:
        cfg.qdrant.prefer_grpc = False

    return cfg


def load_config(path: Path | None = None) -> SnsfIndexConfig:
    src = Path(path) if path else DEFAULT_CONFIG_PATH
    raw: Dict[str, Any] = {}
    if src.exists():
        raw = yaml.safe_load(src.read_text(encoding="utf-8")) or {}
    return _env_override(SnsfIndexConfig(**raw))


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "QdrantConfig",
    "RcpConfig",
    "RetrievalConfig",
    "ScopeConfig",
    "ScopeMode",
    "SnsfApiConfig",
    "SnsfIndexConfig",
    "load_config",
]
