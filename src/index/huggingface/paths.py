"""Filesystem layout for HuggingFace index artifacts.

Single source of truth for paths under `<INDEX_DATA_DIR>/huggingface/`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_INDEX_DATA_DIR = Path("data/index")


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


@dataclass(slots=True, frozen=True)
class HuggingFacePaths:
    """Resolved filesystem paths for the HuggingFace module."""

    root: Path

    @property
    def duckdb_dir(self) -> Path:
        return self.root / "duckdb"

    @property
    def duckdb_path(self) -> Path:
        return self.duckdb_dir / "huggingface.duckdb"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def cards_dir(self) -> Path:
        return self.root / "cards"

    def cards_path_for(self, entity_type: str, repo_id: str) -> Path:
        return self.cards_dir / entity_type / repo_id


def get_huggingface_paths() -> HuggingFacePaths:
    """Resolve `<INDEX_DATA_DIR>/huggingface/` and ensure subdirs exist."""
    root = _resolve_index_data_dir() / "huggingface"
    paths = HuggingFacePaths(root=root)
    paths.duckdb_dir.mkdir(parents=True, exist_ok=True)
    paths.cache_dir.mkdir(parents=True, exist_ok=True)
    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    paths.cards_dir.mkdir(parents=True, exist_ok=True)
    return paths
