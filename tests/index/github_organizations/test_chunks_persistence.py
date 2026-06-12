# tests/index/github_organizations/test_chunks_persistence.py
"""Bug 13 regression guard: github_organizations must keep a DuckDB `chunks`
table and persist into it (it was reported as writing "straight to Qdrant" with
no chunks table — not true in the current shared-base code). This locks in the
consistency with github_users / github_repos at the store level, no network.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.index.github_organizations.storage.duckdb_store import GitHubOrganizationsStore


@pytest.fixture
def store(tmp_path: Path):
    s = GitHubOrganizationsStore.open(tmp_path / "github_organizations.duckdb")
    yield s
    s.close()


def test_chunks_table_exists_and_persists(store):
    # bootstrap (run by open()) must create the chunks table.
    assert store.count("chunks") == 0
    store.upsert_chunk(
        chunk_id="11111111-1111-4111-8111-111111111111",
        entity_type="organizations",
        entity_id="https://github.com/epfl",
        chunk_index=0,
        text="EPFL — École polytechnique fédérale de Lausanne",
        token_count=9,
        vector_id="11111111-1111-4111-8111-111111111111",
    )
    assert store.count("chunks") == 1


def test_upsert_chunk_is_idempotent(store):
    args = dict(
        chunk_id="22222222-2222-4222-8222-222222222222",
        entity_type="organizations",
        entity_id="https://github.com/sdsc-ordes",
        chunk_index=0,
        text="first",
        token_count=1,
        vector_id="22222222-2222-4222-8222-222222222222",
    )
    store.upsert_chunk(**args)
    store.upsert_chunk(**{**args, "text": "updated"})  # same chunk_id
    assert store.count("chunks") == 1  # ON CONFLICT DO UPDATE, no duplicate
