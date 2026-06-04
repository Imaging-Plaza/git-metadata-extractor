"""Config loader for the gitlab_epfl_projects index."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from src.index._gitlab_base.config_base import GitLabIndexConfig, load_gitlab_config

DEFAULT_CONFIG_PATH = Path("config/index/gitlab_epfl_projects.yaml")

GitLabEpflProjectsConfig = GitLabIndexConfig


def load_config(path: Optional[Path] = None) -> GitLabEpflProjectsConfig:
    return load_gitlab_config(
        yaml_path=path or DEFAULT_CONFIG_PATH,
        store_name="gitlab_epfl_projects",
        token_env="GITLAB_EPFL_TOKEN",  # noqa: S106
    )
