"""Shared Pydantic config for the per-entity HuggingFace indices.

Each concrete module (``huggingface_models``, ``huggingface_datasets``,
``huggingface_spaces``, ``huggingface_users``,
``huggingface_organizations``) loads this with its own YAML +
paths dataclass.

Simpler than the legacy ``HuggingFaceIndexConfig`` — the new
per-entity modules ingest specific repo_ids via the v2 API (or a
manual ingest call), so they don't need the ``scope`` /
``discovery`` blocks. Those stay on the catch-all module until
H7 retires it.

The ``huggingface`` block carries ``api_base`` + ``token`` +
``min_card_chars`` + ``full_cards`` — the same shape the HFClient
expects, so the shared ``HFClient`` from ``_huggingface_base.client``
works against this config unchanged.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel

from src.index._github_accounts_base.paths_base import (
    AccountIndexPathsBase,
)

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


class HuggingFaceFetchConfig(BaseModel):
    """Wire-side knobs for the HF Hub client (``HfApi`` wrapper)."""

    api_base: str = "https://huggingface.co"
    per_page: int = 100
    max_concurrency: int = 4
    full_cards: bool = False
    # HF cards (model/dataset/space descriptions, org bios) are typically
    # short. 32 chars filters "title-only" stubs without dropping real
    # but terse entries.
    min_card_chars: int = 32
    token: Optional[str] = None


class QdrantConfig(BaseModel):
    url: str = "http://gme-qdrant:6333"
    prefer_grpc: bool = False
    api_key: Optional[str] = None


class ChunkingConfig(BaseModel):
    size_tokens: int = 512
    overlap_tokens: int = 64
    tokenizer: str = "cl100k_base"


class HFEntityIndexConfigBase(BaseModel):
    """Common config shape for the five per-entity HF indices."""

    rcp: RcpConfig
    huggingface: HuggingFaceFetchConfig
    qdrant: QdrantConfig
    chunking: ChunkingConfig
    paths: AccountIndexPathsBase

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


def load_hf_entity_config(
    *,
    yaml_path: Path,
    paths: AccountIndexPathsBase,
) -> HFEntityIndexConfigBase:
    """Read the YAML, merge env-sourced credentials + qdrant overrides,
    and return the validated config. Env conventions match the existing
    huggingface module: ``RCP_TOKEN``, ``HF_TOKEN``,
    ``INDEX_QDRANT_URL`` / ``INDEX_QDRANT_API_KEY`` /
    ``INDEX_QDRANT_PREFER_GRPC``.
    """
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}

    raw.setdefault("rcp", {})["token"] = _env_str("RCP_TOKEN")
    raw.setdefault("huggingface", {})["token"] = _env_str("HF_TOKEN")
    raw.setdefault("qdrant", {})["api_key"] = _env_str("INDEX_QDRANT_API_KEY")

    if (override := _env_str("INDEX_QDRANT_URL")) is not None:
        raw["qdrant"]["url"] = override
    if (override_b := _env_bool("INDEX_QDRANT_PREFER_GRPC")) is not None:
        raw["qdrant"]["prefer_grpc"] = override_b

    raw["paths"] = paths

    return HFEntityIndexConfigBase(**raw)
