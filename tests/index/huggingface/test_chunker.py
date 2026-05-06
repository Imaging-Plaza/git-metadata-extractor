"""Chunker invariants for the HuggingFace card layout."""

from __future__ import annotations

from src.index.huggingface.embed.chunker import chunk_for_card, chunk_text


def test_chunk_text_handles_empty():
    assert chunk_text("", chunk_tokens=128, overlap=16) == []


def test_chunk_text_single_window():
    out = chunk_text("hello world", chunk_tokens=128, overlap=16)
    assert len(out) == 1
    assert out[0].text == "hello world"
    assert out[0].token_count > 0


def test_chunk_for_card_titles_first():
    chunks = chunk_for_card(
        title="epfl-llm/meditron-7b",
        tags=["medical", "llama"],
        description="Meditron is a clinical LLM.",
        readme="# Meditron\n\nA medical LLM trained at EPFL.",
        chunk_tokens=128,
        overlap=16,
    )
    assert chunks
    # The repo_id must appear first in the first chunk so the embedder sees it.
    assert chunks[0].text.startswith("epfl-llm/meditron-7b")
    assert "Tags: medical, llama" in chunks[0].text


def test_chunk_for_card_no_content():
    chunks = chunk_for_card(
        title="x/y",
        tags=None,
        description=None,
        readme=None,
        chunk_tokens=128,
        overlap=16,
    )
    # Just the title — one chunk.
    assert len(chunks) == 1
    assert chunks[0].text == "x/y"
