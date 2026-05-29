"""Filesystem paths for the zenodo_communities index."""

from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_ROOT = "data/index"
_SOURCE = "zenodo_communities"


def index_data_root() -> Path:
    return Path(os.getenv("INDEX_DATA_DIR", _DEFAULT_ROOT)).expanduser().resolve()


def zenodo_communities_data_dir() -> Path:
    root = index_data_root() / _SOURCE
    root.mkdir(parents=True, exist_ok=True)
    return root


def duckdb_dir() -> Path:
    p = zenodo_communities_data_dir() / "duckdb"
    p.mkdir(parents=True, exist_ok=True)
    return p


def duckdb_path() -> Path:
    return duckdb_dir() / "zenodo_communities.duckdb"
