"""Config loader for the SWISSUbase indexer.

Reads `config/index/swissubase.yaml` and merges in env-sourced credentials
(`RCP_TOKEN`, `INDEX_QDRANT_API_KEY`, `SELENIUM_REMOTE_URL`,
`INDEX_QDRANT_URL`) plus the resolved data dir.

The `rcp` and `qdrant` sub-blocks mirror `OpenAlexIndexConfig` /
`ZenodoIndexConfig` field-for-field so that the same
`RCPEmbeddingClient` / `RCPRerankerClient` / `QdrantStore` reused by the
zenodo indexer also works here at runtime — those clients only access
`config.rcp.*` and `config.qdrant.*` and never import a specific config
module at runtime.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel

from src.index.swissubase.paths import SwissubasePaths, get_swissubase_paths

DEFAULT_CONFIG_PATH = Path("config/index/swissubase.yaml")

TRUE_ENV_VALUES = {"1", "true", "t", "yes", "y", "on"}
FALSE_ENV_VALUES = {"0", "false", "f", "no", "n", "off"}

MISSING_RCP_TOKEN_ERROR = "Missing required environment variable: RCP_TOKEN"
MISSING_SELENIUM_URL_ERROR = "Missing required environment variable: SELENIUM_REMOTE_URL"


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


class CatalogueConfig(BaseModel):
    base_url: str = "https://www.swissubase.ch"
    language: str = "en"
    # Server-side allowlist: {5, 10, 20, 50}. Other values 400.
    page_size: int = 50
    # Polite spacing between catalogue page navigations (seconds).
    page_delay_seconds: float = 1.0
    # Max wait for the rendered Material table on each list page.
    list_render_timeout_seconds: int = 30
    # Max wait for a study detail JSON XHR to come back.
    detail_timeout_seconds: int = 30

    # ---- ID-iteration mode -------------------------------------------------
    # The search-studies endpoint caps deep pagination at ~250 items
    # regardless of filters. We instead enumerate every studyVersionId in a
    # bounded range; per-study endpoints have no such cap. Observed live
    # range is ~1..21,500 with ~58% density, so 25,000 is a safe upper bound.
    id_start: int = 1
    id_end: int = 25000
    # Polite spacing between per-ID requests (seconds). Lower than
    # `page_delay_seconds` because we now make 50× more requests.
    per_id_delay_seconds: float = 0.2


class ScopeConfig(BaseModel):
    """Scope filter applied during ingest.

    swissUbase has no institution filter on the catalogue, so we ingest
    every study and post-filter on the rendered institution string.
    Studies whose institution string matches any pattern below get
    `affiliation_match=TRUE` and are the only ones embedded by default.
    """

    default: str = "epfl_sdsc_ethz"
    # Case-insensitive substrings tested against the institution string.
    epfl_sdsc_ethz_patterns: list[str] = [
        "EPFL",
        "École polytechnique fédérale de Lausanne",
        "Ecole polytechnique fédérale de Lausanne",
        "ETH Zurich",
        "ETHZ",
        "Eidgenössische Technische Hochschule",
        "SDSC",
        "Swiss Data Science Center",
    ]


class SeleniumConfig(BaseModel):
    remote_url: Optional[str] = None
    page_load_timeout_seconds: int = 60
    headless: bool = True


class QdrantConfig(BaseModel):
    url: str = "http://localhost:6333"
    prefer_grpc: bool = False
    api_key: Optional[str] = None


class ChunkingConfig(BaseModel):
    size_tokens: int = 256
    overlap_tokens: int = 64
    tokenizer: str = "cl100k_base"


class SwissubaseIndexConfig(BaseModel):
    rcp: RcpConfig
    catalogue: CatalogueConfig
    scope: ScopeConfig
    selenium: SeleniumConfig
    qdrant: QdrantConfig
    chunking: ChunkingConfig
    paths: SwissubasePaths

    model_config = {"arbitrary_types_allowed": True}

    def require_rcp(self) -> None:
        if not self.rcp.token:
            raise ValueError(MISSING_RCP_TOKEN_ERROR)

    def require_selenium(self) -> None:
        if not self.selenium.remote_url:
            raise ValueError(MISSING_SELENIUM_URL_ERROR)


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


def load_config(path: Optional[Path] = None) -> SwissubaseIndexConfig:
    cfg_path = path or DEFAULT_CONFIG_PATH
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    raw.setdefault("rcp", {})["token"] = _env_str("RCP_TOKEN")
    raw.setdefault("selenium", {})["remote_url"] = _env_str("SELENIUM_REMOTE_URL")
    raw.setdefault("qdrant", {})["api_key"] = _env_str("INDEX_QDRANT_API_KEY")

    if (override := _env_str("INDEX_QDRANT_URL")) is not None:
        raw["qdrant"]["url"] = override
    if (override_b := _env_bool("INDEX_QDRANT_PREFER_GRPC")) is not None:
        raw["qdrant"]["prefer_grpc"] = override_b
    if (override := _env_str("INDEX_SWISSUBASE_SCOPE")) is not None:
        raw.setdefault("scope", {})["default"] = override
    if (override := _env_str("INDEX_SWISSUBASE_ID_END")) is not None:
        raw.setdefault("catalogue", {})["id_end"] = int(override)
    if (override := _env_str("INDEX_SWISSUBASE_ID_START")) is not None:
        raw.setdefault("catalogue", {})["id_start"] = int(override)

    raw["paths"] = get_swissubase_paths()

    return SwissubaseIndexConfig(**raw)
