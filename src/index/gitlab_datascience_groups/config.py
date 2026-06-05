"""Config loader for the gitlab_datascience_groups index."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from src.index._gitlab_base.config_base import GitLabIndexConfig, load_gitlab_config

DEFAULT_CONFIG_PATH = Path("config/index/gitlab_datascience_groups.yaml")

GitLabDatascienceGroupsConfig = GitLabIndexConfig


def load_config(path: Optional[Path] = None) -> GitLabDatascienceGroupsConfig:
    return load_gitlab_config(
        yaml_path=path or DEFAULT_CONFIG_PATH,
        store_name="gitlab_datascience_groups",
        token_env="GITLAB_DATASCIENCE_TOKEN",  # noqa: S106
    )
