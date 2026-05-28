"""Works ingestion: abstract reconstruction + persistence."""

from __future__ import annotations

import pytest

from src.index.openalex.ingest.works import (
    persist_work,
    reconstruct_abstract,
)


@pytest.mark.openalex()
def test_reconstruct_abstract_simple():
    inverted = {"hello": [0], "world": [1]}
    assert reconstruct_abstract(inverted) == "hello world"


@pytest.mark.openalex()
def test_reconstruct_abstract_repeated_token():
    inverted = {"the": [0, 2], "cat": [1], "sat": [3]}
    assert reconstruct_abstract(inverted) == "the cat the sat"


@pytest.mark.openalex()
def test_reconstruct_abstract_empty():
    assert reconstruct_abstract(None) is None
    assert reconstruct_abstract({}) is None


@pytest.mark.openalex()
def test_persist_work_writes_join_tables(tmp_store):
    item = {
        "id": "https://openalex.org/W1",
        "doi": "10.1/x",
        "title": "Hello",
        "abstract_inverted_index": {"abstract": [0], "text": [1]},
        "publication_year": 2024,
        "primary_topic": {"id": "https://openalex.org/T1"},
        "primary_location": {"source": {"id": "https://openalex.org/S1"}},
        "authorships": [
            {
                "author": {"id": "https://openalex.org/A1"},
                "institutions": [{"id": "https://openalex.org/I1"}],
            },
            {
                "author": {"id": "https://openalex.org/A2"},
                "institutions": [{"id": "https://openalex.org/I1"}],
            },
        ],
    }
    work_id = persist_work(tmp_store, item)
    assert work_id == "https://openalex.org/W1"

    row = tmp_store.fetch_work("https://openalex.org/W1")
    assert row["abstract"] == "abstract text"
    assert row["primary_topic_id"] == "https://openalex.org/T1"
    assert row["primary_source_id"] == "https://openalex.org/S1"

    n_authors = tmp_store.connect().execute(
        "SELECT count(*) FROM work_authors WHERE work_id = ?",
        [work_id],
    ).fetchone()[0]
    assert n_authors == 2

    n_inst = tmp_store.connect().execute(
        "SELECT count(*) FROM work_institutions WHERE work_id = ?",
        [work_id],
    ).fetchone()[0]
    assert n_inst == 1  # deduped


@pytest.mark.openalex()
def test_persist_work_skips_when_no_id(tmp_store):
    assert persist_work(tmp_store, {"title": "no id"}) is None
    assert tmp_store.count("works") == 0
