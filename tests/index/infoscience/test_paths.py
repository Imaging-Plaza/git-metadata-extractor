"""Path resolution honours INDEX_DATA_DIR."""

from __future__ import annotations

from pathlib import Path

from src.index.infoscience.paths import (
    infoscience_data_dir,
    raw_items_dir,
    raw_organizations_dir,
    raw_persons_dir,
    text_dir,
    vector_db_dir,
)


def test_data_dir_uses_env_override(isolated_data_dir: Path) -> None:
    root = infoscience_data_dir()
    assert str(root).startswith(str(isolated_data_dir))
    assert root.name == "infoscience"


def test_subdirs_exist_after_call(isolated_data_dir: Path) -> None:
    paths = [raw_items_dir(), raw_persons_dir(), raw_organizations_dir(),
             text_dir(), vector_db_dir()]
    for p in paths:
        assert p.exists() and p.is_dir()
