"""Config loader for the EPFL Graph disciplines indexer.

Reads ``config/index/epfl_graph.yaml`` and merges in env-sourced
credentials (``RCP_TOKEN``, ``INDEX_QDRANT_API_KEY``,
``INDEX_QDRANT_URL``).

The ``rcp`` and ``qdrant`` sub-blocks mirror :class:`OpenAlexIndexConfig`
field-for-field so that openalex's RCPEmbeddingClient / RCPRerankerClient
/ QdrantStore can be reused with an :class:`EpflGraphIndexConfig`
instance at runtime.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel

from src.index.epfl_graph.paths import EpflGraphPaths, get_epfl_graph_paths

DEFAULT_CONFIG_PATH = Path("config/index/epfl_graph.yaml")

TRUE_ENV_VALUES = {"1", "true", "t", "yes", "y", "on"}
FALSE_ENV_VALUES = {"0", "false", "f", "no", "n", "off"}

MISSING_RCP_TOKEN_ERROR = "Missing required environment variable: RCP_TOKEN"  # noqa: S105


class RcpConfig(BaseModel):
    base_url: str
    embedding_model: str
    embedding_dim: int
    query_instruction: str
    reranker_model: str
    batch_size: int = 32
    max_concurrency: int = 4
    timeout_seconds: int = 120
    token: str | None = None


class GraphAIConfig(BaseModel):
    base_url: str = "https://graphai.epfl.ch"
    port: int = 443
    rate_per_second: int = 5
    max_concurrency: int = 4
    anchor_concepts_per_category: int = 12


class QdrantConfig(BaseModel):
    url: str = "http://localhost:6333"
    prefer_grpc: bool = False
    api_key: str | None = None


class FilterConfig(BaseModel):
    min_depth: int = 3


class EpflGraphIndexConfig(BaseModel):
    rcp: RcpConfig
    graphai: GraphAIConfig
    qdrant: QdrantConfig
    filter: FilterConfig
    paths: EpflGraphPaths

    model_config = {"arbitrary_types_allowed": True}

    def require_rcp(self) -> None:
        if not self.rcp.token:
            raise ValueError(MISSING_RCP_TOKEN_ERROR)


def _env_bool(name: str) -> bool | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None
    normalized = raw.strip().lower()
    if normalized in TRUE_ENV_VALUES:
        return True
    if normalized in FALSE_ENV_VALUES:
        return False
    message = f"Invalid boolean value for {name}: {raw!r}"
    raise ValueError(message)


def _env_str(name: str) -> str | None:
    raw = os.getenv(name)
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


def load_config(path: Path | None = None) -> EpflGraphIndexConfig:
    cfg_path = path or DEFAULT_CONFIG_PATH
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    raw.setdefault("rcp", {})["token"] = _env_str("RCP_TOKEN")
    raw.setdefault("qdrant", {})["api_key"] = _env_str("INDEX_QDRANT_API_KEY")

    if (override := _env_str("INDEX_QDRANT_URL")) is not None:
        raw["qdrant"]["url"] = override
    if (override_b := _env_bool("INDEX_QDRANT_PREFER_GRPC")) is not None:
        raw["qdrant"]["prefer_grpc"] = override_b

    raw["paths"] = get_epfl_graph_paths()

    return EpflGraphIndexConfig(**raw)
