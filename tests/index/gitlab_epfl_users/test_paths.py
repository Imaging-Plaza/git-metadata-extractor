from src.index.gitlab_epfl_users.config import load_config
from src.index.gitlab_epfl_users.paths import get_gitlab_epfl_users_paths


def test_duckdb_path_layout():
    p = get_gitlab_epfl_users_paths().duckdb_path
    assert p.as_posix().endswith("gitlab_epfl_users/duckdb/gitlab_epfl_users.duckdb")


def test_config_loads():
    cfg = load_config()
    assert cfg.gitlab.host == "gitlab.epfl.ch"
    assert cfg.gitlab.collection == "gitlab_epfl_users"
