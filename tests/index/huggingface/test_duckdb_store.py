"""DuckDB schema bootstrap + upsert/stream behaviour."""

from __future__ import annotations

import pytest


def test_bootstrap_idempotent(tmp_store):
    tmp_store.bootstrap()
    tmp_store.bootstrap()
    assert tmp_store.count("models") == 0


def test_upsert_model_round_trip(tmp_store):
    row = {
        "repo_id": "epfl-llm/meditron-7b",
        "author": "epfl-llm",
        "sha": "abc123",
        "pipeline_tag": "text-generation",
        "library_name": "transformers",
        "license": "llama2",
        "downloads": 100,
        "downloads_all_time": 1000,
        "likes": 42,
        "gated": False,
        "private": False,
        "created_at": None,
        "last_modified": None,
        "tags": ["medical", "llama"],
        "card_data": {"license": "llama2", "language": "en"},
        "base_models": ["meta-llama/Llama-2-7b"],
    }
    tmp_store.upsert_model(row, raw={"id": "epfl-llm/meditron-7b"})
    fetched = tmp_store.fetch_repo("models", "epfl-llm/meditron-7b")
    assert fetched is not None
    assert fetched["author"] == "epfl-llm"
    assert fetched["downloads"] == 100
    assert tmp_store.count("models") == 1


def test_upsert_org_round_trip(tmp_store):
    tmp_store.upsert_org(slug="epfl-llm", scope="epfl")
    cur = tmp_store.connect().execute("SELECT slug, scope, source FROM orgs")
    rows = cur.fetchall()
    assert rows == [("epfl-llm", "epfl", "seed")]


def test_stream_rows_skips_already_embedded(tmp_store):
    tmp_store.upsert_model({"repo_id": "a/b"}, raw={})
    tmp_store.upsert_model({"repo_id": "c/d"}, raw={})
    tmp_store.upsert_chunk(
        chunk_id="cid-1",
        entity_type="model",
        repo_id="a/b",
        chunk_index=0,
        text="hi",
        token_count=1,
        vector_id="cid-1",
    )
    streamed = list(tmp_store.stream_rows_for_embedding("models"))
    assert {r["repo_id"] for r in streamed} == {"c/d"}


def test_stream_rows_unknown_table(tmp_store):
    with pytest.raises(ValueError, match="Unknown entity table"):
        list(tmp_store.stream_rows_for_embedding("widgets"))
