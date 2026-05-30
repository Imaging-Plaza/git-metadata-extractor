"""Config loader for the github_organizations index."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from src.index._github_accounts_base.config_base import (
    AccountIndexConfigBase,
    load_account_config,
)
from src.index.github_organizations.paths import (
    get_github_organizations_paths,
)

DEFAULT_CONFIG_PATH = Path("config/index/github_organizations.yaml")

GitHubOrganizationsIndexConfig = AccountIndexConfigBase


def load_config(path: Optional[Path] = None) -> GitHubOrganizationsIndexConfig:
    return load_account_config(
        yaml_path=path or DEFAULT_CONFIG_PATH,
        paths=get_github_organizations_paths(),
    )
