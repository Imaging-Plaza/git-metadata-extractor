"""GitHub users index — DuckDB + Qdrant catalog of GitHub user cards.

Each ingested user is one DuckDB row (`users` table) and one or more
Qdrant points (collection ``github_users``) holding a chunked
embedding of their composed text (name + bio + company + location +
blog). Used by v2 extraction for disambiguation: given a candidate
handle or affiliation string, find the most likely matching user.

Module layout follows the existing ``src.index.github_repos`` repo-index
pattern, with the cross-cutting infra factored into
``src.index._github_accounts_base``.
"""

from src.index.github_users.config import GitHubUsersIndexConfig, load_config
from src.index.github_users.models import UserRecord
from src.index.github_users.paths import GitHubUsersPaths, get_github_users_paths
from src.index.github_users.storage.duckdb_store import GitHubUsersStore

__all__ = [
    "GitHubUsersIndexConfig",
    "GitHubUsersPaths",
    "GitHubUsersStore",
    "UserRecord",
    "get_github_users_paths",
    "load_config",
]
