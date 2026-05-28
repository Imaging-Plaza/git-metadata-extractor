"""Config loader for the ETH Research Collection indexer.

Reads `config/index/ethz_research_collection.yaml` and merges in env-sourced credentials
(`RCP_TOKEN`, `ETHZ_RESEARCH_COLLECTION_TOKEN`, `INDEX_QDRANT_*`) plus the resolved data
dir.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

import yaml
from pydantic import BaseModel, Field

from .paths import ethz_research_collection_data_dir

DEFAULT_CONFIG_PATH = Path("config/index/ethz_research_collection.yaml")

_TRUE_ENV_VALUES = {"1", "true", "t", "yes", "y", "on"}
_FALSE_ENV_VALUES = {"0", "false", "f", "no", "n", "off"}


class RcpConfig(BaseModel):
    base_url: str
    embedding_model: str
    embedding_dim: int
    query_instruction: str
    reranker_model: str
    batch_size: int = 16
    max_concurrency: int = 4
    timeout_seconds: int = 120
    token: Optional[str] = None  # populated from RCP_TOKEN at load time


class EthzResearchCollectionConfig(BaseModel):
    base_url: str
    page_size: int = 100
    max_concurrency: int = 4
    token: Optional[str] = None  # populated from ETHZ_RESEARCH_COLLECTION_TOKEN at load time


class FilterConfig(BaseModel):
    terms: List[str] = Field(default_factory=list)


class ChunkingConfig(BaseModel):
    size_tokens: int = 1024
    overlap_tokens: int = 128
    tokenizer: str = "cl100k_base"


class QdrantConfig(BaseModel):
    url: str = "http://qdrant:6333"
    prefer_grpc: bool = False
    api_key: Optional[str] = None  # populated from INDEX_QDRANT_API_KEY at load


class EthzResearchCollectionIndexConfig(BaseModel):
    rcp: RcpConfig
    research_collection: EthzResearchCollectionConfig
    filter: FilterConfig
    chunking: ChunkingConfig
    qdrant: QdrantConfig
    data_dir: Path


def _env_str(name: str) -> Optional[str]:
    raw = os.getenv(name)
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


def _env_bool(name: str) -> Optional[bool]:
    raw = _env_str(name)
    if raw is None:
        return None
    normalised = raw.lower()
    if normalised in _TRUE_ENV_VALUES:
        return True
    if normalised in _FALSE_ENV_VALUES:
        return False
    msg = f"Invalid boolean value for {name}: {raw!r}"
    raise ValueError(msg)


def load_config(path: Optional[Path] = None) -> EthzResearchCollectionIndexConfig:
    """Load + validate the YAML config; merge env tokens and data dir."""
    cfg_path = path or DEFAULT_CONFIG_PATH
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    raw.setdefault("rcp", {})["token"] = _env_str("RCP_TOKEN")
    raw.setdefault("research_collection", {})["token"] = _env_str("ETHZ_RESEARCH_COLLECTION_TOKEN")
    raw.setdefault("qdrant", {})["api_key"] = _env_str("INDEX_QDRANT_API_KEY")

    if (override := _env_str("INDEX_QDRANT_URL")) is not None:
        raw["qdrant"]["url"] = override
    if (override_b := _env_bool("INDEX_QDRANT_PREFER_GRPC")) is not None:
        raw["qdrant"]["prefer_grpc"] = override_b

    raw["data_dir"] = ethz_research_collection_data_dir()

    return EthzResearchCollectionIndexConfig(**raw)
