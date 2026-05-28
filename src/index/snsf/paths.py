"""Filesystem paths for the SNSF P3 index.

Roots at `${INDEX_DATA_DIR:-data/index}/snsf` (shared root with the openalex /
ror / huggingface siblings). Sub-paths are derived constants below;
directories are created on first access.
"""

from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_ROOT = "data/index"
_SOURCE = "snsf"


def index_data_root() -> Path:
    """Top-level multi-source data root (`data/index` by default)."""
    return Path(os.getenv("INDEX_DATA_DIR", _DEFAULT_ROOT)).expanduser().resolve()


def snsf_data_dir() -> Path:
    """Per-source root for SNSF. Creates the directory tree if needed."""
    root = index_data_root() / _SOURCE
    root.mkdir(parents=True, exist_ok=True)
    return root


def duckdb_dir() -> Path:
    p = snsf_data_dir() / "duckdb"
    p.mkdir(parents=True, exist_ok=True)
    return p


def duckdb_path() -> Path:
    return duckdb_dir() / "snsf.duckdb"


def logs_dir() -> Path:
    p = snsf_data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p
