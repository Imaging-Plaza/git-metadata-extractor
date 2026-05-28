"""Subprocess worker for the streaming chunks-embed pipeline.

Runs one group's worth of (article_uuids → chunks → embed → upsert) and
exits, so the OS reclaims every byte of Python heap, RCP connection
buffers, and Qdrant client state. Used by `build_chunks` when
`INFOSCIENCE_EMBED_WORKER_MODE=subprocess` is set.

Protocol:
- stdin: JSON list of article UUIDs.
- stdout (last line): JSON `{"upserted": int, "chunks": int, "skipped_no_text": int}`.
- exit 0 on success; non-zero on any error (parent re-raises).

Why a worker instead of just `gc.collect() + malloc_trim(0)`:
- `gc + malloc_trim` reclaim ~30-50% of free pages but won't undo
  fragmentation accumulated across many groups.
- A worker's RSS dies with `os.exit`. Predictable, bounded peak.
- Cost: ~3-5 s per-group startup (importing tiktoken, httpx,
  qdrant_client, pydantic). For ~80 groups, that's ~7 min added —
  acceptable to avoid 14+ GB RSS slowly creeping toward swap.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import List

from .build import _articles_with_matches, _chunks_for_article
from .config import load_config
from .embed import RCPEmbedder
from .extract_matches import matches_by_uuid
from .paths import text_dir
from .store import CHUNKS_COLLECTION, QdrantStore, upsert_chunks


async def _run(uuids: List[str]) -> dict:
    cfg = load_config()
    matches = matches_by_uuid()
    matches_subset = {u: matches[u] for u in uuids if u in matches}
    articles = _articles_with_matches(matches_subset)

    chunk_records = []
    skipped_no_text = 0
    for article in articles:
        tp = text_dir() / f"{article.article_uuid}.txt"
        if not tp.exists():
            skipped_no_text += 1
            continue
        text = tp.read_text(encoding="utf-8", errors="replace")
        chunk_records.extend(_chunks_for_article(cfg, article, text))

    if not chunk_records:
        return {"upserted": 0, "chunks": 0, "skipped_no_text": skipped_no_text}

    store = QdrantStore.from_config(cfg)
    store.ensure_collection(CHUNKS_COLLECTION)

    async with RCPEmbedder(cfg.rcp) as embedder:
        texts = [c.text for c in chunk_records]
        embeddings = await embedder.embed_passages_batched(texts)

    upserted = upsert_chunks(store, chunk_records, embeddings)
    return {
        "upserted": upserted,
        "chunks": len(chunk_records),
        "skipped_no_text": skipped_no_text,
    }


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    payload = sys.stdin.read()
    uuids = json.loads(payload) if payload.strip() else []
    if not isinstance(uuids, list):
        sys.stderr.write("expected a JSON list of article UUIDs on stdin\n")
        return 2
    summary = asyncio.run(_run([str(u) for u in uuids]))
    # Last line of stdout = JSON summary (parent reads only the last line).
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
