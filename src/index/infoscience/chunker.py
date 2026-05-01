"""Token-aware text chunker.

Greedy paragraph-aware splitter using the configured tiktoken encoding.
Chunks target `chunking.size_tokens` with `chunking.overlap_tokens` of
trailing context carried into the next chunk.
"""

from __future__ import annotations

from typing import List

import tiktoken

from .config import ChunkingConfig

_PARAGRAPH_SEP = "\n\n"


def _split_paragraphs(text: str) -> List[str]:
    paras = [p.strip() for p in text.split(_PARAGRAPH_SEP)]
    return [p for p in paras if p]


def chunk_text(text: str, cfg: ChunkingConfig) -> List[str]:
    """Return a list of chunk strings."""
    if not text.strip():
        return []
    enc = tiktoken.get_encoding(cfg.tokenizer)
    paragraphs = _split_paragraphs(text)
    if not paragraphs:
        return []

    chunks: List[str] = []
    current_tokens: List[int] = []
    for para in paragraphs:
        para_tokens = enc.encode(para)
        # If a single paragraph exceeds the budget, flush and slice it.
        if len(para_tokens) > cfg.size_tokens:
            if current_tokens:
                chunks.append(enc.decode(current_tokens))
                current_tokens = current_tokens[-cfg.overlap_tokens:] if cfg.overlap_tokens else []
            for i in range(0, len(para_tokens), cfg.size_tokens):
                window = para_tokens[i : i + cfg.size_tokens]
                chunks.append(enc.decode(window))
            current_tokens = para_tokens[-cfg.overlap_tokens:] if cfg.overlap_tokens else []
            continue

        sep_tokens = enc.encode(_PARAGRAPH_SEP) if current_tokens else []
        if len(current_tokens) + len(sep_tokens) + len(para_tokens) <= cfg.size_tokens:
            current_tokens.extend(sep_tokens)
            current_tokens.extend(para_tokens)
        else:
            chunks.append(enc.decode(current_tokens))
            tail = current_tokens[-cfg.overlap_tokens:] if cfg.overlap_tokens else []
            current_tokens = list(tail) + sep_tokens + para_tokens

    if current_tokens:
        chunks.append(enc.decode(current_tokens))

    return [c.strip() for c in chunks if c.strip()]
