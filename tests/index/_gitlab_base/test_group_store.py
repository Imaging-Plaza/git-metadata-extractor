# tests/index/_gitlab_base/test_group_store.py
from __future__ import annotations

from pathlib import Path

import pytest

from src.index._gitlab_base.group_store import GitLabGroupStore
from src.index._gitlab_base.models import GitLabGroupRecord

_URL = "https://gitlab.epfl.ch/groups/mygroup"


@pytest.fixture
def store(tmp_path: Path):
    s = GitLabGroupStore.open(tmp_path / "gitlab_epfl_groups.duckdb")
    yield s
    s.close()


def test_upsert_round_trip_keeps_url_id(store):
    store.upsert_group(GitLabGroupRecord(
        group_id=_URL, host="gitlab.epfl.ch", full_path="mygroup",
        name="My Group", description="A test group", visibility="public",
    ))
    row = store.fetch_group(_URL)
    assert row["group_id"] == _URL
    assert row["full_path"] == "mygroup"
    assert store.count("groups") == 1


def test_stream_skips_embedded(store):
    store.upsert_group(GitLabGroupRecord(group_id=_URL, host="h", full_path="mygroup"))
    assert len(list(store.stream_rows_for_embedding("groups"))) == 1
    store.upsert_chunk(chunk_id="c1", entity_type="groups", entity_id=_URL,
                       chunk_index=0, text="x", token_count=1, vector_id="c1")
    assert list(store.stream_rows_for_embedding("groups")) == []
