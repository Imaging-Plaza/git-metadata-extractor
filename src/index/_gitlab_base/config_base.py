"""Shared Pydantic config base for GitLab project indices.

Each concrete index module calls ``load_gitlab_config`` with its own YAML
path, store name, and token env-var name. The ``rcp`` / ``qdrant`` /
``chunking`` blocks are structurally identical to the existing GitHub index
configs so any helper that only sees ``config.rcp.*`` / ``config.qdrant.*``
at runtime works unchanged against a GitLab config.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel

from src.index._gitlab_base.paths_base import GitLabIndexPathsBase, resolve_gitlab_paths


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


class QdrantConfig(BaseModel):
    url: str = "http://gme-qdrant:6333"
    prefer_grpc: bool = False
    api_key: Optional[str] = None


class ChunkingConfig(BaseModel):
    size_tokens: int = 512
    overlap_tokens: int = 64
    tokenizer: str = "cl100k_base"


class GitLabFetchConfig(BaseModel):
    """Wire-side knobs for a GitLab host."""

    host: str
    token: Optional[str] = None
    per_page: int = 100
    min_card_chars: int = 64
    collection: str


class GitLabIndexConfig(BaseModel):
    rcp: RcpConfig
    qdrant: QdrantConfig
    chunking: ChunkingConfig
    gitlab: GitLabFetchConfig
    paths: GitLabIndexPathsBase

    model_config = {"arbitrary_types_allowed": True}


def _env_bool(name: str) -> Optional[bool]:
    _true = {"1", "true", "t", "yes", "y", "on"}
    _false = {"0", "false", "f", "no", "n", "off"}
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None
    normalized = raw.strip().lower()
    if normalized in _true:
        return True
    if normalized in _false:
        return False
    message = f"Invalid boolean value for {name}: {raw!r}"
    raise ValueError(message)


def _env_str(name: str) -> Optional[str]:
    raw = os.getenv(name)
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


def load_gitlab_config(
    *,
    yaml_path: Path,
    store_name: str,
    token_env: str,
) -> GitLabIndexConfig:
    """Read the YAML, merge env-sourced credentials and Qdrant overrides,
    and return the validated config.

    Env vars honoured:
    - ``RCP_TOKEN`` → ``rcp.token``
    - ``<token_env>`` → ``gitlab.token``
    - ``INDEX_QDRANT_URL`` → ``qdrant.url``
    - ``INDEX_QDRANT_API_KEY`` → ``qdrant.api_key``
    - ``INDEX_QDRANT_PREFER_GRPC`` → ``qdrant.prefer_grpc``
    """
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}

    raw.setdefault("rcp", {})["token"] = _env_str("RCP_TOKEN")
    raw.setdefault("gitlab", {})["token"] = _env_str(token_env)
    raw.setdefault("qdrant", {})["api_key"] = _env_str("INDEX_QDRANT_API_KEY")

    if (override := _env_str("INDEX_QDRANT_URL")) is not None:
        raw["qdrant"]["url"] = override
    if (override_b := _env_bool("INDEX_QDRANT_PREFER_GRPC")) is not None:
        raw["qdrant"]["prefer_grpc"] = override_b

    raw["paths"] = resolve_gitlab_paths(store_name)

    return GitLabIndexConfig(**raw)
