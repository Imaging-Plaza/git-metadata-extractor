"""Chunk-ID determinism + namespace alignment."""

from __future__ import annotations

from src.index.huggingface.embed.pipeline import _chunk_id


def test_chunk_id_deterministic():
    a = _chunk_id("model", "epfl-llm/meditron-7b", 0)
    b = _chunk_id("model", "epfl-llm/meditron-7b", 0)
    assert a == b


def test_chunk_id_changes_with_index():
    a = _chunk_id("model", "epfl-llm/meditron-7b", 0)
    b = _chunk_id("model", "epfl-llm/meditron-7b", 1)
    assert a != b


def test_chunk_id_changes_with_repo():
    a = _chunk_id("model", "epfl-llm/meditron-7b", 0)
    b = _chunk_id("model", "epfl-llm/meditron-70b", 0)
    assert a != b


def test_chunk_id_changes_with_entity_type():
    a = _chunk_id("model", "x/y", 0)
    b = _chunk_id("dataset", "x/y", 0)
    assert a != b
