"""Config loader for the huggingface_papers index.

Reads `config/index/huggingface_papers.yaml` and merges env-sourced
credentials (RCP_TOKEN, HF_TOKEN, INDEX_QDRANT_API_KEY) plus the
resolved data dir.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel

from src.index.huggingface_papers.paths import (
    HuggingFacePapersPaths,
    get_huggingface_papers_paths,
)

DEFAULT_CONFIG_PATH = Path("config/index/huggingface_papers.yaml")

MISSING_RCP_TOKEN_ERROR = "Missing required environment variable: RCP_TOKEN"
# HF_TOKEN is optional for the public Papers endpoint, but required for
# the (rare) authenticated paper access. We don't `require_hf()` by
# default — anonymous access works for the public-paper case.


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


class HuggingFacePapersFetchConfig(BaseModel):
    """Wire-side knobs for the HF Papers REST client."""

    api_base: str = "https://huggingface.co"
    max_concurrency: int = 4
    # Papers always have a title; abstract may be missing for very
    # new daily-papers entries. 32 chars is enough to filter
    # "title-only" stubs.
    min_card_chars: int = 32
    # Optional HF token — falls back to anonymous when None.
    token: Optional[str] = None


class QdrantConfig(BaseModel):
    url: str = "http://gme-qdrant:6333"
    prefer_grpc: bool = False
    api_key: Optional[str] = None


class ChunkingConfig(BaseModel):
    # Paper cards (title + abstract + authors) are typically 300-800
    # tokens — single-chunk semantics work for most papers, with
    # occasional splits for long abstracts.
    size_tokens: int = 768
    overlap_tokens: int = 64
    tokenizer: str = "cl100k_base"


class HuggingFacePapersIndexConfig(BaseModel):
    rcp: RcpConfig
    huggingface: HuggingFacePapersFetchConfig
    qdrant: QdrantConfig
    chunking: ChunkingConfig
    paths: HuggingFacePapersPaths

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


def _env_bool(name: str) -> Optional[bool]:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "f", "no", "n", "off"}:
        return False
    return None


def load_config(path: Optional[Path] = None) -> HuggingFacePapersIndexConfig:
    cfg_path = path or DEFAULT_CONFIG_PATH
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    raw.setdefault("rcp", {})["token"] = _env_str("RCP_TOKEN")
    raw.setdefault("huggingface", {})["token"] = _env_str("HF_TOKEN")
    raw.setdefault("qdrant", {})["api_key"] = _env_str("INDEX_QDRANT_API_KEY")

    if (override := _env_str("INDEX_QDRANT_URL")) is not None:
        raw["qdrant"]["url"] = override
    if (override_b := _env_bool("INDEX_QDRANT_PREFER_GRPC")) is not None:
        raw["qdrant"]["prefer_grpc"] = override_b

    raw["paths"] = get_huggingface_papers_paths()

    return HuggingFacePapersIndexConfig(**raw)
