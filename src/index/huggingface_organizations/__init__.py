"""HuggingFace organizations index — DuckDB + Qdrant catalog of HF org namespaces.

Sibling to ``src.index.huggingface_users``. Splits the legacy ``orgs``
table by ``namespace_kind='org'``. ``HFClient.namespace_overview``
returns ``(kind, overview)``; this module skips records where
``kind == 'user'`` (those belong in ``huggingface_users``).
"""

from src.index.huggingface_organizations.config import (
    HuggingFaceOrganizationsIndexConfig,
    load_config,
)
from src.index.huggingface_organizations.models import HFOrgRecord
from src.index.huggingface_organizations.paths import (
    HuggingFaceOrganizationsPaths,
    get_huggingface_organizations_paths,
)
from src.index.huggingface_organizations.storage.duckdb_store import (
    HuggingFaceOrganizationsStore,
)

__all__ = [
    "HFOrgRecord",
    "HuggingFaceOrganizationsIndexConfig",
    "HuggingFaceOrganizationsPaths",
    "HuggingFaceOrganizationsStore",
    "get_huggingface_organizations_paths",
    "load_config",
]
