"""Config loader for the OAM-CH indexer.

Reads ``config/index/oamonitor.yaml`` and merges in env credentials
(``RCP_TOKEN``, ``INDEX_QDRANT_API_KEY``) plus the resolved data dir.

The ``rcp`` and ``qdrant`` sub-blocks mirror ``OpenAlexIndexConfig``
field-for-field so the OpenAlex RCP embedder/reranker and ``QdrantStore``
can be reused with an ``OamonitorIndexConfig`` instance at runtime —
they only TYPE_CHECK against the OpenAlex config and never import it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel

from src.index.oamonitor.paths import OamonitorPaths, get_oamonitor_paths

DEFAULT_CONFIG_PATH = Path("config/index/oamonitor.yaml")

TRUE_ENV_VALUES = {"1", "true", "t", "yes", "y", "on"}
FALSE_ENV_VALUES = {"0", "false", "f", "no", "n", "off"}

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


class OamonitorAPIConfig(BaseModel):
    """Upstream OAM-CH Mongo-proxy API."""

    base_url: str = "https://oam.oamonitor.ch/api/data/public"
    page_size: int = 1000
    rate_per_minute: int = 60
    max_concurrency: int = 4
    timeout_seconds: int = 60


class QdrantConfig(BaseModel):
    url: str = "http://localhost:6333"
    prefer_grpc: bool = False
    api_key: Optional[str] = None


class ChunkingConfig(BaseModel):
    size_tokens: int = 256
    overlap_tokens: int = 64
    tokenizer: str = "cl100k_base"


class OamonitorIndexConfig(BaseModel):
    rcp: RcpConfig
    oamonitor: OamonitorAPIConfig
    qdrant: QdrantConfig
    chunking: ChunkingConfig
    paths: OamonitorPaths

    model_config = {"arbitrary_types_allowed": True}

    def require_rcp(self) -> None:
        if not self.rcp.token:
            raise ValueError(MISSING_RCP_TOKEN_ERROR)


def _env_bool(name: str) -> Optional[bool]:
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


def _env_str(name: str) -> Optional[str]:
    raw = os.getenv(name)
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


def load_config(path: Optional[Path] = None) -> OamonitorIndexConfig:
    cfg_path = path or DEFAULT_CONFIG_PATH
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    raw.setdefault("rcp", {})["token"] = _env_str("RCP_TOKEN")
    raw.setdefault("qdrant", {})["api_key"] = _env_str("INDEX_QDRANT_API_KEY")

    if (override := _env_str("INDEX_QDRANT_URL")) is not None:
        raw["qdrant"]["url"] = override
    if (override_b := _env_bool("INDEX_QDRANT_PREFER_GRPC")) is not None:
        raw["qdrant"]["prefer_grpc"] = override_b
    if (override := _env_str("OAMONITOR_API_BASE_URL")) is not None:
        raw.setdefault("oamonitor", {})["base_url"] = override

    raw["paths"] = get_oamonitor_paths()

    return OamonitorIndexConfig(**raw)
