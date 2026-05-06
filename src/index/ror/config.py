"""Config loader for the ROR indexer.

Reads `config/index/ror.yaml` and merges in the RCP token from `RCP_TOKEN`
plus the resolved data dir. Mirrors the shape of `src/index/infoscience/config.py`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

import yaml
from pydantic import BaseModel, Field, field_validator

from .paths import ror_data_dir

DEFAULT_CONFIG_PATH = Path("config/index/ror.yaml")

EPFL_ROR_ID = "https://ror.org/02s376052"
ETHZ_ROR_ID = "https://ror.org/05a28rw58"


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


class ScopeConfig(BaseModel):
    mode: str = "epfl_ethz"
    seeds: List[str] = Field(default_factory=lambda: [EPFL_ROR_ID, ETHZ_ROR_ID])
    expand: List[str] = Field(default_factory=lambda: ["parent", "child", "related"])
    max_depth: int = 2

    @field_validator("mode")
    @classmethod
    def _check_mode(cls, v: str) -> str:
        allowed = {"epfl_ethz", "switzerland", "europe", "worldwide"}
        if v not in allowed:
            msg = f"scope.mode must be one of {sorted(allowed)}, got {v!r}"
            raise ValueError(msg)
        return v


class RorDumpConfig(BaseModel):
    zenodo_concept_doi: str = "10.5281/zenodo.6347574"


class RetrievalConfig(BaseModel):
    top_k: int = 50
    rerank_top_k: int = 10


class QdrantConfig(BaseModel):
    url: str = "http://localhost:6333"
    prefer_grpc: bool = False
    api_key: Optional[str] = None  # populated from INDEX_QDRANT_API_KEY at load time
    collection_prefix: str = "ror"


class RorIndexConfig(BaseModel):
    rcp: RcpConfig
    scope: ScopeConfig = Field(default_factory=ScopeConfig)
    ror_dump: RorDumpConfig = Field(default_factory=RorDumpConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    qdrant: QdrantConfig = Field(default_factory=QdrantConfig)
    data_dir: Path

    def collection_name(self) -> str:
        return f"{self.qdrant.collection_prefix}_{self.scope.mode}"


def load_config(path: Optional[Path] = None) -> RorIndexConfig:
    """Load + validate the YAML config; merge `RCP_TOKEN` and resolved data dir."""
    cfg_path = path or DEFAULT_CONFIG_PATH
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))

    raw.setdefault("rcp", {})["token"] = os.getenv("RCP_TOKEN")

    raw.setdefault("qdrant", {})["api_key"] = os.getenv("INDEX_QDRANT_API_KEY")
    if (override := os.getenv("INDEX_QDRANT_URL")):
        raw["qdrant"]["url"] = override.strip()
    if (override := os.getenv("INDEX_QDRANT_PREFER_GRPC")):
        raw["qdrant"]["prefer_grpc"] = override.strip().lower() in {"1", "true", "yes", "on"}

    raw["data_dir"] = ror_data_dir()

    return RorIndexConfig(**raw)
