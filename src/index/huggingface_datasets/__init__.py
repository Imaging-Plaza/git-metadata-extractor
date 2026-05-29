"""HuggingFace datasets index — DuckDB + Qdrant catalog of HF dataset cards.

One of five per-entity HuggingFace indices. Pairs with
``huggingface_models`` / ``huggingface_spaces`` / etc., each owning
its own DuckDB file + Qdrant collection.
"""

from src.index.huggingface_datasets.config import (
    HuggingFaceDatasetsIndexConfig,
    load_config,
)
from src.index.huggingface_datasets.models import DatasetRecord
from src.index.huggingface_datasets.paths import (
    HuggingFaceDatasetsPaths,
    get_huggingface_datasets_paths,
)
from src.index.huggingface_datasets.storage.duckdb_store import (
    HuggingFaceDatasetsStore,
)

__all__ = [
    "DatasetRecord",
    "HuggingFaceDatasetsIndexConfig",
    "HuggingFaceDatasetsPaths",
    "HuggingFaceDatasetsStore",
    "get_huggingface_datasets_paths",
    "load_config",
]
