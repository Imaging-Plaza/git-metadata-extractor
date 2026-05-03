"""Config loader for the ORCID indexer.

Reads `config/index/orcid.yaml` and merges in env-sourced credentials
(`RCP_TOKEN`, `INDEX_QDRANT_API_KEY`) plus a few targeted env overrides.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel

from src.index.orcid.paths import OrcidPaths, get_orcid_paths

DEFAULT_CONFIG_PATH = Path("config/index/orcid.yaml")

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
    token: Optional[str] = None  # populated from RCP_TOKEN at load time


class OrcidApiConfig(BaseModel):
    base_url: str = "https://pub.orcid.org/v3.0"
    timeout_seconds: int = 20
    search_max_rows: int = 200
    max_retries: int = 6
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 60.0
    request_min_interval_seconds: float = 1.5
    user_agent: Optional[str] = "git-metadata-extractor-orcid-index/0.1"
    # OAuth 2-legged (client_credentials) for the public-API rate uplift.
    # Both client_id and client_secret must be set for the token fetch to fire;
    # if either is missing, the provider falls back to anonymous access.
    oauth_token_url: str = "https://orcid.org/oauth/token"
    oauth_scope: str = "/read-public"
    client_id: Optional[str] = None       # populated from ORCID_CLIENT_ID
    client_secret: Optional[str] = None   # populated from ORCID_CLIENT_SECRET


class ScopeConfig(BaseModel):
    ror: str
    country: str
    affiliation_aliases: list[str] = []


class DiscoveryConfig(BaseModel):
    source: str = "both"  # openalex | orcid_search | both
    openalex_db: str = "data/index/openalex/duckdb/openalex.duckdb"


class QdrantConfig(BaseModel):
    url: str = "http://localhost:6333"
    prefer_grpc: bool = False
    api_key: Optional[str] = None  # populated from INDEX_QDRANT_API_KEY


class ChunkingConfig(BaseModel):
    size_tokens: int = 256
    overlap_tokens: int = 64
    tokenizer: str = "cl100k_base"


class OrcidIndexConfig(BaseModel):
    rcp: RcpConfig
    orcid: OrcidApiConfig
    scope: ScopeConfig
    discovery: DiscoveryConfig
    qdrant: QdrantConfig
    chunking: ChunkingConfig
    paths: OrcidPaths

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


def _active_scope(scope: Optional[str]) -> str:
    """Resolve the scope being operated on, mirroring `paths._resolve_scope`."""
    if scope:
        return scope
    raw = os.getenv("INDEX_ORCID_SCOPE")
    if raw and raw.strip():
        return raw.strip()
    return "epfl"


def _env_int(name: str) -> Optional[int]:
    raw = _env_str(name)
    if raw is None:
        return None
    return int(raw)


def _env_float(name: str) -> Optional[float]:
    raw = _env_str(name)
    if raw is None:
        return None
    return float(raw)


def load_config(
    path: Optional[Path] = None,
    *,
    scope: Optional[str] = None,
) -> OrcidIndexConfig:
    """Load + validate the YAML config; merge env tokens, env overrides, paths."""
    cfg_path = path or DEFAULT_CONFIG_PATH
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    # Per-scope alias resolution: when YAML provides a dict-form
    # `scope.affiliation_aliases: { epfl: [...], switzerland: [...] }`,
    # collapse it to the active scope's list at load-time so callers can keep
    # reading `config.scope.affiliation_aliases: list[str]`.
    scope_block = raw.setdefault("scope", {})
    aliases_raw = scope_block.get("affiliation_aliases", [])
    if isinstance(aliases_raw, dict):
        active = _active_scope(scope)
        scope_block["affiliation_aliases"] = list(aliases_raw.get(active, []))

    raw.setdefault("rcp", {})["token"] = _env_str("RCP_TOKEN")
    raw.setdefault("qdrant", {})["api_key"] = _env_str("INDEX_QDRANT_API_KEY")
    raw.setdefault("orcid", {})["client_id"] = _env_str("ORCID_CLIENT_ID")
    raw.setdefault("orcid", {})["client_secret"] = _env_str("ORCID_CLIENT_SECRET")

    if (override := _env_str("INDEX_QDRANT_URL")) is not None:
        raw["qdrant"]["url"] = override
    if (override_b := _env_bool("INDEX_QDRANT_PREFER_GRPC")) is not None:
        raw["qdrant"]["prefer_grpc"] = override_b
    if (override := _env_str("INDEX_ORCID_SCOPE_ROR")) is not None:
        raw.setdefault("scope", {})["ror"] = override
    if (override := _env_str("INDEX_ORCID_SCOPE_COUNTRY")) is not None:
        raw.setdefault("scope", {})["country"] = override
    if (override := _env_str("INDEX_ORCID_DISCOVERY_SOURCE")) is not None:
        raw.setdefault("discovery", {})["source"] = override
    if (override := _env_str("INDEX_ORCID_OPENALEX_DB")) is not None:
        raw.setdefault("discovery", {})["openalex_db"] = override

    if (override_i := _env_int("INDEX_ORCID_MAX_RETRIES")) is not None:
        raw.setdefault("orcid", {})["max_retries"] = override_i
    if (override_f := _env_float("INDEX_ORCID_BASE_DELAY_SECONDS")) is not None:
        raw.setdefault("orcid", {})["base_delay_seconds"] = override_f
    if (override_f := _env_float("INDEX_ORCID_MAX_DELAY_SECONDS")) is not None:
        raw.setdefault("orcid", {})["max_delay_seconds"] = override_f
    if (override_f := _env_float("INDEX_ORCID_REQUEST_MIN_INTERVAL_SECONDS")) is not None:
        raw.setdefault("orcid", {})["request_min_interval_seconds"] = override_f
    if (override := _env_str("INDEX_ORCID_USER_AGENT")) is not None:
        raw.setdefault("orcid", {})["user_agent"] = override

    raw["paths"] = get_orcid_paths(scope)

    return OrcidIndexConfig(**raw)
