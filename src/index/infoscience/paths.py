"""Filesystem paths for the Infoscience index.

Root resolves to `${INDEX_DATA_DIR:-data/index}/infoscience` and is created
on first access. Sub-paths are derived constants below.
"""

from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_ROOT = "data/index"
_SOURCE = "infoscience"


def index_data_root() -> Path:
    """Top-level multi-source data root (`data/index` by default)."""
    return Path(os.getenv("INDEX_DATA_DIR", _DEFAULT_ROOT)).expanduser().resolve()


def infoscience_data_dir() -> Path:
    """Per-source root for Infoscience. Creates the directory tree if needed."""
    root = index_data_root() / _SOURCE
    root.mkdir(parents=True, exist_ok=True)
    return root


def raw_items_dir() -> Path:
    p = infoscience_data_dir() / "raw" / "items"
    p.mkdir(parents=True, exist_ok=True)
    return p


def raw_persons_dir() -> Path:
    p = infoscience_data_dir() / "raw" / "persons"
    p.mkdir(parents=True, exist_ok=True)
    return p


def raw_organizations_dir() -> Path:
    p = infoscience_data_dir() / "raw" / "organizations"
    p.mkdir(parents=True, exist_ok=True)
    return p


def text_dir() -> Path:
    p = infoscience_data_dir() / "text"
    p.mkdir(parents=True, exist_ok=True)
    return p


def vector_db_dir() -> Path:
    """Persistent root for the vector store (ChromaDB)."""
    p = infoscience_data_dir() / "chroma"
    p.mkdir(parents=True, exist_ok=True)
    return p


def discover_state_path() -> Path:
    return infoscience_data_dir() / "discover_state.json"


def matches_path() -> Path:
    return infoscience_data_dir() / "matches.jsonl"


def relations_path() -> Path:
    return infoscience_data_dir() / "relations.jsonl"


def persons_set_path() -> Path:
    return infoscience_data_dir() / "persons.txt"


def organizations_set_path() -> Path:
    return infoscience_data_dir() / "organizations.txt"


def duckdb_path() -> Path:
    """SQLite-style structured store for articles / persons / orgs / chunks."""
    p = infoscience_data_dir() / "duckdb"
    p.mkdir(parents=True, exist_ok=True)
    return p / "infoscience.duckdb"


def dumps_dir() -> Path:
    p = infoscience_data_dir() / "dumps"
    p.mkdir(parents=True, exist_ok=True)
    return p
