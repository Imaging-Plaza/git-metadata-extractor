"""Shared infrastructure for the GitHub user/org account indices.

Both `src.index.github_users` and `src.index.github_organizations` ingest
a single GitHub account card (`GET /users/{login}` or `GET /orgs/{org}`),
upsert it to DuckDB, embed the composed text into a Qdrant collection,
and expose a semantic-search endpoint.

The two modules differ only in:

  - the wire endpoint (user vs org)
  - the persisted record shape (some fields are user-only — `bio`,
    `company`, `twitter_username` — others are org-only — `description`,
    `followers` is on both but means different things)
  - the embedding text composition
  - the collection name + duckdb file path

Everything else — config loading from yaml + env, the embed/upsert
flush loop, the qdrant retry policy, the semantic search shape — is
identical between the two. This module owns the shared pieces as
plain helpers (no class hierarchy) so each concrete module stays
small and easy to read.

The existing `src.index.github_repos.ingest.github_client.GitHubClient` is
reused verbatim — it already has `get_user(login)` and
`get_organization(org_name)` methods backed by the same multi-token /
`ProviderCache` flow the repo index uses.
"""

from src.index._github_accounts_base.config_base import (
    AccountIndexConfigBase,
    load_account_config,
)
from src.index._github_accounts_base.embed_base import embed_accounts_async
from src.index._github_accounts_base.paths_base import (
    AccountIndexPathsBase,
    resolve_account_paths,
)
from src.index._github_accounts_base.retrieval_base import (
    account_semantic_search_async,
)
from src.index._github_accounts_base.storage_base import (
    bootstrap_schema,
    count_table,
    fetch_one,
    stream_unembedded,
    upsert_chunk,
)

__all__ = [
    "AccountIndexConfigBase",
    "AccountIndexPathsBase",
    "account_semantic_search_async",
    "bootstrap_schema",
    "count_table",
    "embed_accounts_async",
    "fetch_one",
    "load_account_config",
    "resolve_account_paths",
    "stream_unembedded",
    "upsert_chunk",
]
