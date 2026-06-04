# tests/index/_gitlab_base/test_project_store.py
from __future__ import annotations

from pathlib import Path

import pytest

from src.index._gitlab_base.models import GitLabProjectRecord
from src.index._gitlab_base.project_store import GitLabProjectStore

_URL = "https://gitlab.epfl.ch/group/proj"


@pytest.fixture
def store(tmp_path: Path):
    s = GitLabProjectStore.open(tmp_path / "gitlab_epfl_projects.duckdb")
    yield s
    s.close()


def test_upsert_round_trip_keeps_url_id(store):
    store.upsert_project(GitLabProjectRecord(
        project_id=_URL, host="gitlab.epfl.ch", full_path="group/proj",
        name="proj", topics=["ml"], star_count=3,
    ))
    row = store.fetch_project(_URL)
    assert row["project_id"] == _URL
    assert row["full_path"] == "group/proj"
    assert store.count("projects") == 1


def test_stream_skips_embedded(store):
    store.upsert_project(GitLabProjectRecord(project_id=_URL, host="h", full_path="group/proj"))
    assert len(list(store.stream_rows_for_embedding("projects"))) == 1
    store.upsert_chunk(chunk_id="c1", entity_type="projects", entity_id=_URL,
                       chunk_index=0, text="x", token_count=1, vector_id="c1")
    assert list(store.stream_rows_for_embedding("projects")) == []
