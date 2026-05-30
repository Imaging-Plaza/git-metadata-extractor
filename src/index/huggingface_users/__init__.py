"""HuggingFace users index — DuckDB + Qdrant catalog of HF user namespaces.

Splits the legacy ``orgs`` table by ``namespace_kind='user'``. The
HF client's ``namespace_overview(slug)`` returns ``(kind, overview)``;
this module only persists records where ``kind == 'user'`` (the
sibling ``huggingface_organizations`` module handles the org case).
"""

from src.index.huggingface_users.config import (
    HuggingFaceUsersIndexConfig,
    load_config,
)
from src.index.huggingface_users.models import HFUserRecord
from src.index.huggingface_users.paths import (
    HuggingFaceUsersPaths,
    get_huggingface_users_paths,
)
from src.index.huggingface_users.storage.duckdb_store import (
    HuggingFaceUsersStore,
)

__all__ = [
    "HFUserRecord",
    "HuggingFaceUsersIndexConfig",
    "HuggingFaceUsersPaths",
    "HuggingFaceUsersStore",
    "get_huggingface_users_paths",
    "load_config",
]
