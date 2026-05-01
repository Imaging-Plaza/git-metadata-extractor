"""Config loader for the OpenAlex indexer.

Reads `config/index/openalex.yaml` and merges in env-sourced credentials
(`RCP_TOKEN`, `OPENALEX_MAILTO`, `INDEX_QDRANT_API_KEY`) plus the resolved
data dir.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel

from src.index.openalex.paths import OpenAlexPaths, get_openalex_paths

DEFAULT_CONFIG_PATH = Path("config/index/openalex.yaml")

TRUE_ENV_VALUES = {"1", "true", "t", "yes", "y", "on"}
FALSE_ENV_VALUES = {"0", "false", "f", "no", "n", "off"}

MISSING_MAILTO_ERROR = (
    "Missing required environment variable: OPENALEX_MAILTO "
    "(needed for the OpenAlex polite pool)"
)
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
    token: Optional[str] = None  # populated from RCP_TOKEN at load time


class OpenAlexConfig(BaseModel):
    base_url: str = "https://api.openalex.org"
    per_page: int = 200
    max_concurrency: int = 4
    mailto: Optional[str] = None  # populated from OPENALEX_MAILTO at load time


class ScopeConfig(BaseModel):
    ror: str
    country: str


class QdrantConfig(BaseModel):
    url: str = "http://localhost:6333"
    prefer_grpc: bool = False
    api_key: Optional[str] = None  # populated from INDEX_QDRANT_API_KEY at load time


class ChunkingConfig(BaseModel):
    size_tokens: int = 256
    overlap_tokens: int = 64
    tokenizer: str = "cl100k_base"


class OpenAlexIndexConfig(BaseModel):
    rcp: RcpConfig
    openalex: OpenAlexConfig
    scope: ScopeConfig
    qdrant: QdrantConfig
    chunking: ChunkingConfig
    paths: OpenAlexPaths

    model_config = {"arbitrary_types_allowed": True}

    def require_ingest(self) -> None:
        """Validate config required to run the OpenAlex ingest path."""
        if not self.openalex.mailto:
            raise ValueError(MISSING_MAILTO_ERROR)

    def require_rcp(self) -> None:
        """Validate config required to call the RCP embedding/rerank endpoints."""
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


def load_config(path: Optional[Path] = None) -> OpenAlexIndexConfig:
    """Load + validate the YAML config; merge env tokens, env overrides, paths."""
    cfg_path = path or DEFAULT_CONFIG_PATH
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    # Inject secrets from env.
    raw.setdefault("rcp", {})["token"] = _env_str("RCP_TOKEN")
    raw.setdefault("openalex", {})["mailto"] = _env_str("OPENALEX_MAILTO")
    raw.setdefault("qdrant", {})["api_key"] = _env_str("INDEX_QDRANT_API_KEY")

    # Targeted env overrides for fields users commonly tune at runtime.
    if (override := _env_str("INDEX_QDRANT_URL")) is not None:
        raw["qdrant"]["url"] = override
    if (override_b := _env_bool("INDEX_QDRANT_PREFER_GRPC")) is not None:
        raw["qdrant"]["prefer_grpc"] = override_b
    if (override := _env_str("INDEX_OPENALEX_SCOPE_ROR")) is not None:
        raw.setdefault("scope", {})["ror"] = override
    if (override := _env_str("INDEX_OPENALEX_SCOPE_COUNTRY")) is not None:
        raw.setdefault("scope", {})["country"] = override

    raw["paths"] = get_openalex_paths()

    return OpenAlexIndexConfig(**raw)
