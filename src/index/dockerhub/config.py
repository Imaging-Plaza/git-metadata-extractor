"""Config loader for the Docker Hub indexer.

Reads `config/index/dockerhub.yaml` and merges in env-sourced credentials
(`RCP_TOKEN`, `DOCKERHUB_TOKEN`, `INDEX_QDRANT_API_KEY`) plus the resolved
data dir.

The `rcp` and `qdrant` sub-blocks mirror `OpenAlexIndexConfig`
field-for-field so the shared openalex RCP / Qdrant clients can be reused
at runtime — they only TYPE_CHECK against the OpenAlex config and never
import it at runtime. Same pattern as `src.index.github_repos`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel

from src.index.dockerhub.paths import DockerhubPaths, get_dockerhub_paths

DEFAULT_CONFIG_PATH = Path("config/index/dockerhub.yaml")

MISSING_RCP_TOKEN_ERROR = "Missing required environment variable: RCP_TOKEN"


class RcpConfig(BaseModel):
    base_url: str
    embedding_model: str
    embedding_dim: int
    query_instruction: str
    reranker_model: str
    batch_size: int = 32
    max_concurrency: int = 4
    timeout_seconds: int = 120
    token: Optional[str] = None


class DockerhubConfig(BaseModel):
    api_base: str = "https://hub.docker.com/v2"
    tags_limit: int = 50
    min_card_chars: int = 64
    full_description_max_bytes: int = 1_048_576
    token: Optional[str] = None


class QdrantConfig(BaseModel):
    url: str = "http://gme-qdrant:6333"
    prefer_grpc: bool = False
    api_key: Optional[str] = None


class ChunkingConfig(BaseModel):
    size_tokens: int = 512
    overlap_tokens: int = 64
    tokenizer: str = "cl100k_base"


class DockerhubIndexConfig(BaseModel):
    rcp: RcpConfig
    dockerhub: DockerhubConfig
    qdrant: QdrantConfig
    chunking: ChunkingConfig
    paths: DockerhubPaths

    model_config = {"arbitrary_types_allowed": True}

    def require_rcp(self) -> None:
        if not self.rcp.token:
            raise ValueError(MISSING_RCP_TOKEN_ERROR)


def _env_str(name: str) -> Optional[str]:
    raw = os.getenv(name)
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


def load_config(path: Optional[Path] = None) -> DockerhubIndexConfig:
    cfg_path = path or DEFAULT_CONFIG_PATH
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    raw.setdefault("rcp", {})["token"] = _env_str("RCP_TOKEN")
    raw.setdefault("dockerhub", {})["token"] = _env_str("DOCKERHUB_TOKEN")
    raw.setdefault("qdrant", {})["api_key"] = _env_str("INDEX_QDRANT_API_KEY")

    if (override := _env_str("INDEX_QDRANT_URL")) is not None:
        raw["qdrant"]["url"] = override

    raw["paths"] = get_dockerhub_paths()

    return DockerhubIndexConfig(**raw)
