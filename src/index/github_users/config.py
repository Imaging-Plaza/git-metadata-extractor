"""Config loader for the github_users index.

Reads `config/index/github_users.yaml` and merges env-sourced
credentials (RCP_TOKEN, GME_GITHUB_TOKEN, INDEX_QDRANT_API_KEY) plus
the resolved data dir.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from src.index._github_accounts_base.config_base import (
    AccountIndexConfigBase,
    load_account_config,
)
from src.index.github_users.paths import get_github_users_paths

DEFAULT_CONFIG_PATH = Path("config/index/github_users.yaml")

GitHubUsersIndexConfig = AccountIndexConfigBase


def load_config(path: Optional[Path] = None) -> GitHubUsersIndexConfig:
    return load_account_config(
        yaml_path=path or DEFAULT_CONFIG_PATH,
        paths=get_github_users_paths(),
    )
