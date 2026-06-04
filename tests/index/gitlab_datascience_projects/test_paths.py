from src.index.gitlab_datascience_projects.config import load_config
from src.index.gitlab_datascience_projects.paths import get_gitlab_datascience_projects_paths


def test_duckdb_path_layout():
    p = get_gitlab_datascience_projects_paths().duckdb_path
    assert p.as_posix().endswith("gitlab_datascience_projects/duckdb/gitlab_datascience_projects.duckdb")


def test_config_loads():
    cfg = load_config()
    assert cfg.gitlab.host == "gitlab.datascience.ch"
    assert cfg.gitlab.collection == "gitlab_datascience_projects"
