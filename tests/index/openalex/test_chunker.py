"""Sliding-window chunker behavior."""

from __future__ import annotations

import pytest

from src.index.openalex.embed.chunker import (
    chunk_for_simple_entity,
    chunk_for_work,
    chunk_text,
)


@pytest.mark.openalex()
def test_empty_text_returns_no_chunks():
    assert chunk_text("", chunk_tokens=10, overlap=2) == []


@pytest.mark.openalex()
def test_short_text_single_chunk():
    chunks = chunk_text("hello world", chunk_tokens=100, overlap=10)
    assert len(chunks) == 1
    assert chunks[0].index == 0
    assert chunks[0].text == "hello world"
    assert chunks[0].token_count > 0


@pytest.mark.openalex()
def test_long_text_produces_multiple_chunks_with_overlap():
    text = " ".join(f"word{i}" for i in range(500))
    chunks = chunk_text(text, chunk_tokens=64, overlap=16)
    assert len(chunks) > 1
    indices = [c.index for c in chunks]
    assert indices == list(range(len(chunks)))
    for chunk in chunks[:-1]:
        assert chunk.token_count <= 64
    # Last chunk may be smaller if the encoded length isn't divisible.
    assert chunks[-1].token_count <= 64


@pytest.mark.openalex()
def test_invalid_overlap_raises():
    with pytest.raises(ValueError, match="overlap"):
        chunk_text("abc", chunk_tokens=10, overlap=10)


@pytest.mark.openalex()
def test_invalid_chunk_tokens_raises():
    with pytest.raises(ValueError, match="chunk_tokens"):
        chunk_text("abc", chunk_tokens=0, overlap=0)


@pytest.mark.openalex()
def test_chunk_for_work_concats_title_and_abstract():
    chunks = chunk_for_work(
        "Hello",
        "Detailed abstract.",
        chunk_tokens=100,
        overlap=10,
    )
    assert len(chunks) == 1
    assert "Hello" in chunks[0].text
    assert "Detailed abstract" in chunks[0].text


@pytest.mark.openalex()
def test_chunk_for_work_handles_missing_abstract():
    chunks = chunk_for_work(
        "Just a title",
        None,
        chunk_tokens=100,
        overlap=10,
    )
    assert len(chunks) == 1
    assert chunks[0].text == "Just a title"


@pytest.mark.openalex()
def test_chunk_for_simple_entity_short_circuits_for_empty_input():
    assert chunk_for_simple_entity(None, None, chunk_tokens=10, overlap=2) == []
