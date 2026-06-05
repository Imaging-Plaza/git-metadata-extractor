from src.index.gitlab_epfl_groups.config import load_config
from src.index.gitlab_epfl_groups.paths import get_gitlab_epfl_groups_paths


def test_duckdb_path_layout():
    p = get_gitlab_epfl_groups_paths().duckdb_path
    assert p.as_posix().endswith("gitlab_epfl_groups/duckdb/gitlab_epfl_groups.duckdb")


def test_config_loads():
    cfg = load_config()
    assert cfg.gitlab.host == "gitlab.epfl.ch"
    assert cfg.gitlab.collection == "gitlab_epfl_groups"
