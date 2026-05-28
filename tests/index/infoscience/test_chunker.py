"""Token-aware chunker."""

from __future__ import annotations

import tiktoken

from src.index.infoscience.chunker import chunk_text
from src.index.infoscience.config import ChunkingConfig


def test_short_text_one_chunk() -> None:
    cfg = ChunkingConfig(size_tokens=200, overlap_tokens=20)
    out = chunk_text("Hello world. " * 5, cfg)
    assert len(out) == 1
    assert out[0].startswith("Hello world.")


def test_paragraph_aware_split_respects_size() -> None:
    cfg = ChunkingConfig(size_tokens=120, overlap_tokens=10)
    enc = tiktoken.get_encoding(cfg.tokenizer)
    paras = ["Lorem ipsum dolor sit amet. " * 30,
             "Consectetur adipiscing elit. " * 30,
             "Sed do eiusmod tempor incididunt. " * 30]
    text = "\n\n".join(paras)
    out = chunk_text(text, cfg)
    assert len(out) >= 3
    for ch in out:
        assert len(enc.encode(ch)) <= cfg.size_tokens + cfg.overlap_tokens + 5


def test_empty_input_returns_empty_list() -> None:
    cfg = ChunkingConfig(size_tokens=100, overlap_tokens=10)
    assert chunk_text("", cfg) == []
    assert chunk_text("   \n\n   ", cfg) == []
