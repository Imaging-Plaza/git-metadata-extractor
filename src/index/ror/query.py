"""Public query API: semantic RAG over the embedded subset, plus lexical
lookup over the full ROR dump.

`query_rag` runs FAISS retrieval and reranks via the RCP cross-encoder.
`lookup_dump` searches the full registry by ROR ID, name tokens, and/or
country code — no RCP calls. `query(mode='auto')` tries RAG first and falls
back to `lookup_dump` when the top score is below `score_floor`.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import List, Literal, Optional

import numpy as np

from .config import RorIndexConfig
from .dump_index import DumpIndex, get_dump_index
from .embed import embed_query
from .models import DumpMatch, ScoredRecord
from .paths import dump_dir
from .qdrant_store import QdrantRorStore
from .rerank import rerank

logger = logging.getLogger(__name__)


def _resolve_dump_json_path() -> Optional[Path]:
    """Find the most-recently-modified `*-ror-data*.json` under the dump dir."""
    base = dump_dir()
    candidates: List[Path] = []
    for child in base.glob("*/*.json"):
        name = child.name
        if "ror-data" in name:
            candidates.append(child)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


async def query_rag(
    cfg: RorIndexConfig,
    text: str,
    *,
    top_k: Optional[int] = None,
    rerank_top_k: Optional[int] = None,
    country: Optional[str] = None,
) -> List[ScoredRecord]:
    """Embed query → Qdrant search → rerank → return top results."""
    top_k = top_k or cfg.retrieval.top_k
    rerank_top_k = rerank_top_k or cfg.retrieval.rerank_top_k

    qvec = await embed_query(cfg.rcp, text, normalize=True)
    store = QdrantRorStore(cfg)
    candidates = store.search(
        cfg.scope.mode,
        query_vector=np.asarray(qvec, dtype=np.float32).tolist(),
        top_k=top_k,
        country=country,
    )
    if not candidates:
        return []

    candidate_texts = [c["text"] for c in candidates]
    rerank_results = await rerank(
        cfg.rcp, text, candidate_texts, top_n=rerank_top_k,
    )
    out: List[ScoredRecord] = []
    for r in rerank_results[:rerank_top_k]:
        if r.index < 0 or r.index >= len(candidates):
            continue
        c = candidates[r.index]
        out.append(ScoredRecord(
            ror_id=c["ror_id"],
            name=c["name"],
            score=r.score,
            record=c.get("record") or {},
        ))
    return out


def _load_full_dump_index(cfg: RorIndexConfig) -> DumpIndex:
    json_path = _resolve_dump_json_path()
    if json_path is None:
        msg = (
            "No ROR dump JSON found under the dump cache. "
            "Run `python -m src.index.ror build` first to download the dump."
        )
        raise FileNotFoundError(msg)
    return get_dump_index(json_path)


def lookup_dump(
    cfg: RorIndexConfig,
    *,
    text: Optional[str] = None,
    ror_id: Optional[str] = None,
    country: Optional[str] = None,
    limit: int = 20,
) -> List[DumpMatch]:
    """Lexical / exact lookup over the full ROR dump (no RCP calls)."""
    if not (text or ror_id or country):
        msg = "lookup_dump requires at least one of text, ror_id, country."
        raise ValueError(msg)
    idx = _load_full_dump_index(cfg)
    return idx.search(text=text, ror_id=ror_id, country=country, limit=limit)


async def query(
    cfg: RorIndexConfig,
    text: str,
    *,
    mode: Literal["auto", "rag", "dump"] = "auto",
    score_floor: float = 0.0,
) -> List[ScoredRecord] | List[DumpMatch]:
    """Convenience wrapper: RAG if `mode!="dump"` and there's a hit above floor."""
    if mode == "dump":
        return lookup_dump(cfg, text=text)
    rag_hits = await query_rag(cfg, text)
    if mode == "rag":
        return rag_hits
    if rag_hits and rag_hits[0].score > score_floor:
        return rag_hits
    return lookup_dump(cfg, text=text)


def query_rag_sync(cfg: RorIndexConfig, text: str, **kwargs) -> List[ScoredRecord]:
    return asyncio.run(query_rag(cfg, text, **kwargs))


def query_sync(cfg: RorIndexConfig, text: str, **kwargs):
    return asyncio.run(query(cfg, text, **kwargs))


__all__: List[str] = [
    "lookup_dump",
    "query",
    "query_rag",
    "query_rag_sync",
    "query_sync",
]
