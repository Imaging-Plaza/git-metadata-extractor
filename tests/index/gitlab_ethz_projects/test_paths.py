from src.index.gitlab_ethz_projects.config import load_config
from src.index.gitlab_ethz_projects.paths import get_gitlab_ethz_projects_paths


def test_duckdb_path_layout():
    p = get_gitlab_ethz_projects_paths().duckdb_path
    assert p.as_posix().endswith("gitlab_ethz_projects/duckdb/gitlab_ethz_projects.duckdb")


def test_config_loads():
    cfg = load_config()
    assert cfg.gitlab.host == "gitlab.ethz.ch"
    assert cfg.gitlab.collection == "gitlab_ethz_projects"
