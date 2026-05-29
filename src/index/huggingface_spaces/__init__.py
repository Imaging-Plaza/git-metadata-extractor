"""HuggingFace spaces index — DuckDB + Qdrant catalog of HF Space cards."""

from src.index.huggingface_spaces.config import (
    HuggingFaceSpacesIndexConfig,
    load_config,
)
from src.index.huggingface_spaces.models import SpaceRecord
from src.index.huggingface_spaces.paths import (
    HuggingFaceSpacesPaths,
    get_huggingface_spaces_paths,
)
from src.index.huggingface_spaces.storage.duckdb_store import (
    HuggingFaceSpacesStore,
)

__all__ = [
    "HuggingFaceSpacesIndexConfig",
    "HuggingFaceSpacesPaths",
    "HuggingFaceSpacesStore",
    "SpaceRecord",
    "get_huggingface_spaces_paths",
    "load_config",
]
