"""Token-based sliding-window chunker built on tiktoken.

The chunker is encoding-agnostic — `cl100k_base` is the default since it's
a reasonable proxy for many modern tokenizers. The Qwen3 tokenizer differs,
but for window *sizing* (not training) the proxy is fine: we err on the
side of slightly smaller chunks, which is safer for context limits.
"""

from __future__ import annotations

from dataclasses import dataclass

import tiktoken

DEFAULT_ENCODING = "cl100k_base"


@dataclass(slots=True, frozen=True)
class Chunk:
    index: int
    text: str
    token_count: int


def _get_encoder(name: str = DEFAULT_ENCODING) -> tiktoken.Encoding:
    return tiktoken.get_encoding(name)


def chunk_text(
    text: str,
    *,
    chunk_tokens: int,
    overlap: int,
    encoding_name: str = DEFAULT_ENCODING,
) -> list[Chunk]:
    """Slide a window of `chunk_tokens` with `overlap` over the encoded text."""
    if chunk_tokens <= 0:
        message = "chunk_tokens must be positive"
        raise ValueError(message)
    if overlap < 0 or overlap >= chunk_tokens:
        message = "overlap must be in [0, chunk_tokens)"
        raise ValueError(message)
    if not text:
        return []
    enc = _get_encoder(encoding_name)
    tokens = enc.encode(text)
    if not tokens:
        return []
    if len(tokens) <= chunk_tokens:
        return [Chunk(index=0, text=text, token_count=len(tokens))]
    chunks: list[Chunk] = []
    step = chunk_tokens - overlap
    start = 0
    idx = 0
    while start < len(tokens):
        window = tokens[start : start + chunk_tokens]
        if not window:
            break
        chunks.append(
            Chunk(index=idx, text=enc.decode(window), token_count=len(window)),
        )
        if start + chunk_tokens >= len(tokens):
            break
        start += step
        idx += 1
    return chunks


def chunk_for_work(
    title: str | None,
    abstract: str | None,
    *,
    chunk_tokens: int,
    overlap: int,
) -> list[Chunk]:
    """Concatenate title + abstract and chunk."""
    parts = [p for p in (title, abstract) if p]
    if not parts:
        return []
    text = "\n\n".join(parts)
    return chunk_text(text, chunk_tokens=chunk_tokens, overlap=overlap)


def chunk_for_simple_entity(
    display_name: str | None,
    summary: str | None = None,
    *,
    chunk_tokens: int,
    overlap: int,
) -> list[Chunk]:
    """Single-pass chunking for entities whose useful text is short."""
    parts = [p for p in (display_name, summary) if p]
    if not parts:
        return []
    text = " — ".join(parts)
    return chunk_text(text, chunk_tokens=chunk_tokens, overlap=overlap)
