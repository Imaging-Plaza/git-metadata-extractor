"""Filesystem paths for the ROR index.

Roots at `${INDEX_DATA_DIR:-data/index}/ror` (shared root with the infoscience
sibling). Sub-paths are derived constants below; directories are created on
first access.
"""

from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_ROOT = "data/index"
_SOURCE = "ror"


def index_data_root() -> Path:
    """Top-level multi-source data root (`data/index` by default)."""
    return Path(os.getenv("INDEX_DATA_DIR", _DEFAULT_ROOT)).expanduser().resolve()


def ror_data_dir() -> Path:
    """Per-source root for ROR. Creates the directory tree if needed."""
    root = index_data_root() / _SOURCE
    root.mkdir(parents=True, exist_ok=True)
    return root


def dump_dir() -> Path:
    p = ror_data_dir() / "dump"
    p.mkdir(parents=True, exist_ok=True)
    return p


def index_dir(scope_mode: str) -> Path:
    """Built-index directory for one scope mode (`epfl_ethz`, `switzerland`, …)."""
    p = ror_data_dir() / "index" / scope_mode
    p.mkdir(parents=True, exist_ok=True)
    return p


def faiss_path(scope_mode: str) -> Path:
    return index_dir(scope_mode) / "index.faiss"


def records_path(scope_mode: str) -> Path:
    return index_dir(scope_mode) / "records.jsonl"


def manifest_path(scope_mode: str) -> Path:
    return index_dir(scope_mode) / "manifest.json"
