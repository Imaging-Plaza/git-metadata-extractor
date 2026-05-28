"""Filesystem layout for ORCID index artifacts.

Each scope (`epfl`, `ch`, ...) lands in its own subtree
(`<INDEX_DATA_DIR>/orcid-<scope>/`) so EPFL and Switzerland runs stay
isolated and independently rebuildable. Scope is selected via the
`INDEX_ORCID_SCOPE` env var (or by passing `scope=` to `get_orcid_paths`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_INDEX_DATA_DIR = Path("data/index")
DEFAULT_SCOPE = "epfl"


def _resolve_repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _resolve_index_data_dir() -> Path:
    raw = os.getenv("INDEX_DATA_DIR")
    if raw and raw.strip():
        candidate = Path(raw.strip()).expanduser()
        if candidate.is_absolute():
            return candidate
        return _resolve_repo_root() / candidate
    return _resolve_repo_root() / DEFAULT_INDEX_DATA_DIR


def _resolve_scope(scope: str | None) -> str:
    if scope:
        return scope
    raw = os.getenv("INDEX_ORCID_SCOPE")
    if raw and raw.strip():
        return raw.strip()
    return DEFAULT_SCOPE


@dataclass(slots=True, frozen=True)
class OrcidPaths:
    """Resolved paths for one ORCID scope."""

    root: Path
    scope: str

    @property
    def duckdb_dir(self) -> Path:
        return self.root / "duckdb"

    @property
    def duckdb_path(self) -> Path:
        return self.duckdb_dir / "orcid.duckdb"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def seeds_path(self) -> Path:
        return self.root / "seeds.jsonl"

    def collection_name(self, entity: str) -> str:
        """Qdrant collection name, namespaced by scope."""
        return f"orcid_{self.scope}_{entity}"


def get_orcid_paths(scope: str | None = None) -> OrcidPaths:
    """Resolve `<INDEX_DATA_DIR>/orcid-<scope>/` and ensure subdirs exist."""
    resolved_scope = _resolve_scope(scope)
    root = _resolve_index_data_dir() / f"orcid-{resolved_scope}"
    paths = OrcidPaths(root=root, scope=resolved_scope)
    paths.duckdb_dir.mkdir(parents=True, exist_ok=True)
    paths.cache_dir.mkdir(parents=True, exist_ok=True)
    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    return paths
