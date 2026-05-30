"""HuggingFace models index — DuckDB + Qdrant catalog of HF model cards.

Each ingested model is one DuckDB row (`models` table, keyed on the
HF ``repo_id``) and one or more Qdrant points (collection
``huggingface_models``) holding an embedding of the composed text
(repo_id + description from card + library + license + tags).

One of five per-entity HuggingFace indices that replace the catch-all
``src.index.huggingface``. Built on the shared
``src.index._huggingface_base`` infra (HFClient + config base) plus
the storage/embed/retrieval helpers in ``src.index._github_accounts_base``.
"""

from src.index.huggingface_models.config import (
    HuggingFaceModelsIndexConfig,
    load_config,
)
from src.index.huggingface_models.models import ModelRecord
from src.index.huggingface_models.paths import (
    HuggingFaceModelsPaths,
    get_huggingface_models_paths,
)
from src.index.huggingface_models.storage.duckdb_store import (
    HuggingFaceModelsStore,
)

__all__ = [
    "HuggingFaceModelsIndexConfig",
    "HuggingFaceModelsPaths",
    "HuggingFaceModelsStore",
    "ModelRecord",
    "get_huggingface_models_paths",
    "load_config",
]
