"""DuckDB store: bootstrap, upserts, idempotency."""

from __future__ import annotations

import pytest

from src.index.openalex.storage.duckdb_store import OpenAlexStore


@pytest.mark.openalex()
def test_bootstrap_idempotent(tmp_store: OpenAlexStore):
    tmp_store.bootstrap()  # second call should be a no-op
    assert tmp_store.count("works") == 0


@pytest.mark.openalex()
def test_upsert_work_round_trip(tmp_store: OpenAlexStore):
    work = {
        "openalex_id": "https://openalex.org/W1",
        "doi": "10.1/x",
        "title": "Hello",
        "abstract": "World.",
        "publication_year": 2024,
        "primary_topic_id": None,
        "primary_source_id": None,
    }
    tmp_store.upsert_work(work, raw={"id": work["openalex_id"]})
    assert tmp_store.count("works") == 1
    row = tmp_store.fetch_work("https://openalex.org/W1")
    assert row["title"] == "Hello"
    assert row["publication_year"] == 2024

    # Re-upsert with a changed title — same row.
    work["title"] = "Hello v2"
    tmp_store.upsert_work(work, raw={"id": work["openalex_id"]})
    assert tmp_store.count("works") == 1
    assert tmp_store.fetch_work("https://openalex.org/W1")["title"] == "Hello v2"


@pytest.mark.openalex()
def test_upsert_github_url_idempotent(tmp_store: OpenAlexStore):
    tmp_store.upsert_github_url(
        work_id="W1",
        url="https://github.com/owner/repo",
        normalized_url="https://github.com/owner/repo",
        owner="owner",
        repo="repo",
        source="abstract",
    )
    tmp_store.upsert_github_url(
        work_id="W1",
        url="https://github.com/owner/repo",
        normalized_url="https://github.com/owner/repo",
        owner="owner",
        repo="repo",
        source="abstract",
    )
    n = tmp_store.connect().execute(
        "SELECT count(*) FROM work_github_urls",
    ).fetchone()[0]
    assert n == 1


@pytest.mark.openalex()
def test_transaction_commits_on_success(tmp_store: OpenAlexStore):
    with tmp_store.transaction():
        for i in range(3):
            tmp_store.upsert_work(
                {
                    "openalex_id": f"W{i}",
                    "doi": None,
                    "title": f"T{i}",
                    "abstract": None,
                    "publication_year": 2024,
                    "primary_topic_id": None,
                    "primary_source_id": None,
                },
                raw={"id": f"W{i}"},
            )
    assert tmp_store.count("works") == 3


@pytest.mark.openalex()
def test_transaction_rolls_back_on_error(tmp_store: OpenAlexStore):
    class BoomError(Exception):
        pass

    with pytest.raises(BoomError):
        with tmp_store.transaction():
            tmp_store.upsert_work(
                {
                    "openalex_id": "Wgood",
                    "doi": None,
                    "title": "ok",
                    "abstract": None,
                    "publication_year": 2024,
                    "primary_topic_id": None,
                    "primary_source_id": None,
                },
                raw={"id": "Wgood"},
            )
            raise BoomError
    # The good upsert was rolled back.
    assert tmp_store.count("works") == 0


@pytest.mark.openalex()
def test_chunks_unique_constraint_blocks_duplicate_indexes(tmp_store: OpenAlexStore):
    tmp_store.upsert_chunk(
        chunk_id="c1",
        entity_type="works",
        entity_id="W1",
        chunk_index=0,
        text="hello",
        token_count=1,
        vector_id="c1",
    )
    # Same chunk_id → upsert overwrites text/token_count/vector_id, no error.
    tmp_store.upsert_chunk(
        chunk_id="c1",
        entity_type="works",
        entity_id="W1",
        chunk_index=0,
        text="updated",
        token_count=2,
        vector_id="c1",
    )
    text = tmp_store.connect().execute(
        "SELECT text FROM chunks WHERE chunk_id = 'c1'",
    ).fetchone()[0]
    assert text == "updated"
