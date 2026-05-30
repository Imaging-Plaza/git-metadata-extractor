"""Shared Pydantic config base for the GitHub account indices.

Each concrete index module instantiates this with its own paths
dataclass + yaml file. The `rcp`/`github`/`qdrant`/`chunking` blocks
are identical to the existing `GitHubIndexConfig` shape — same RCP
deployment, same Qdrant cluster, same token plumbing — so any RCP +
Qdrant helper that only sees `config.rcp.*` / `config.qdrant.*` at
runtime (e.g. the openalex `RCPEmbeddingClient` / `QdrantStore`) works
unchanged against an account index config.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel

from src.index._github_accounts_base.paths_base import AccountIndexPathsBase

TRUE_ENV_VALUES = {"1", "true", "t", "yes", "y", "on"}
FALSE_ENV_VALUES = {"0", "false", "f", "no", "n", "off"}

MISSING_RCP_TOKEN_ERROR = "Missing required environment variable: RCP_TOKEN"
MISSING_GME_GITHUB_TOKEN_ERROR = "Missing required environment variable: GME_GITHUB_TOKEN"


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


class GitHubAccountFetchConfig(BaseModel):
    """Wire-side knobs. Smaller `min_card_chars` than the repo index —
    user/org bios are typically short; the repo index's 64-char floor
    would drop most real accounts."""

    api_base: str = "https://api.github.com"
    max_concurrency: int = 4
    min_card_chars: int = 16
    token: Optional[str] = None


class QdrantConfig(BaseModel):
    url: str = "http://gme-qdrant:6333"
    prefer_grpc: bool = False
    api_key: Optional[str] = None


class ChunkingConfig(BaseModel):
    size_tokens: int = 512
    overlap_tokens: int = 64
    tokenizer: str = "cl100k_base"


class AccountIndexConfigBase(BaseModel):
    """Common config shape for the github user/org indices.

    Concrete subclasses don't add fields; the variation between users
    and orgs is in the YAML defaults (`query_instruction`, scope seeds,
    etc.), not the schema.
    """

    rcp: RcpConfig
    github: GitHubAccountFetchConfig
    qdrant: QdrantConfig
    chunking: ChunkingConfig
    paths: AccountIndexPathsBase

    model_config = {"arbitrary_types_allowed": True}

    def require_rcp(self) -> None:
        if not self.rcp.token:
            raise ValueError(MISSING_RCP_TOKEN_ERROR)

    def require_github(self) -> None:
        if not self.github.token:
            raise ValueError(MISSING_GME_GITHUB_TOKEN_ERROR)


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


def load_account_config(
    *,
    yaml_path: Path,
    paths: AccountIndexPathsBase,
) -> AccountIndexConfigBase:
    """Read the YAML, merge env-sourced credentials + qdrant overrides,
    and return the validated config. Same env-var conventions as the
    existing repo index: `RCP_TOKEN`, `GME_GITHUB_TOKEN`,
    `INDEX_QDRANT_URL`, `INDEX_QDRANT_API_KEY`, `INDEX_QDRANT_PREFER_GRPC`.
    """
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}

    raw.setdefault("rcp", {})["token"] = _env_str("RCP_TOKEN")
    raw.setdefault("github", {})["token"] = _env_str("GME_GITHUB_TOKEN")
    raw.setdefault("qdrant", {})["api_key"] = _env_str("INDEX_QDRANT_API_KEY")

    if (override := _env_str("INDEX_QDRANT_URL")) is not None:
        raw["qdrant"]["url"] = override
    if (override_b := _env_bool("INDEX_QDRANT_PREFER_GRPC")) is not None:
        raw["qdrant"]["prefer_grpc"] = override_b

    raw["paths"] = paths

    return AccountIndexConfigBase(**raw)
