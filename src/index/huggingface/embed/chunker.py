# TODO: dedupe with src/index/openalex/embed/chunker.py — kept as a copy
# while the user defers extracting a shared chunker module.
"""Token-based sliding-window chunker built on tiktoken.

Encoding-agnostic — `cl100k_base` is the default since it's a reasonable
proxy for many modern tokenizers. The Qwen3 tokenizer differs but for
window *sizing* the proxy is fine: we err on the side of slightly smaller
chunks, which is safer for context limits.
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


def chunk_for_card(
    *,
    title: str,
    tags: list[str] | None,
    description: str | None,
    readme: str | None,
    chunk_tokens: int,
    overlap: int,
    encoding_name: str = DEFAULT_ENCODING,
) -> list[Chunk]:
    """Build the embedding text for a HuggingFace card and chunk it.

    Layout: title (line 1) → comma-joined tags (line 2 if any) → description
    (paragraph if any) → README body (rest). The first chunk thus always
    leads with the title + tags, which is what the embedding model sees first.
    """
    parts: list[str] = [title]
    if tags:
        parts.append("Tags: " + ", ".join(tags))
    if description:
        parts.append(description.strip())
    if readme:
        parts.append(readme.strip())
    text = "\n\n".join(parts)
    return chunk_text(
        text,
        chunk_tokens=chunk_tokens,
        overlap=overlap,
        encoding_name=encoding_name,
    )
