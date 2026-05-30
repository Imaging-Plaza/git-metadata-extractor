"""GitHub organizations index — DuckDB + Qdrant catalog of GitHub org cards.

Each ingested org is one DuckDB row (`organizations` table) and one
or more Qdrant points (collection ``github_organizations``) holding a
chunked embedding of their composed text (name + description + blog +
location). Used by v2 extraction for disambiguation: given an
organization name or handle, find the most likely matching org.

Mirrors `src.index.github_users` — both modules sit on the shared
``src.index._github_accounts_base`` infrastructure.
"""

from src.index.github_organizations.config import (
    GitHubOrganizationsIndexConfig,
    load_config,
)
from src.index.github_organizations.models import OrgRecord
from src.index.github_organizations.paths import (
    GitHubOrganizationsPaths,
    get_github_organizations_paths,
)
from src.index.github_organizations.storage.duckdb_store import (
    GitHubOrganizationsStore,
)

__all__ = [
    "GitHubOrganizationsIndexConfig",
    "GitHubOrganizationsPaths",
    "GitHubOrganizationsStore",
    "OrgRecord",
    "get_github_organizations_paths",
    "load_config",
]
