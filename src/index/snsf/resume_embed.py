"""Resume an interrupted `embed --scope <X>` run.

Queries Qdrant for already-upserted point IDs (= grant_numbers) in the
target collection, subtracts them from the scope's grant set in DuckDB,
embeds *only the missing* grants via RCP, and upserts.

Idempotent: re-running after a clean run is a no-op (all IDs already
present → 0 missing → 0 RCP calls).

Run:
    .venv/bin/python -m src.index.snsf.resume_embed [--scope SCOPE]

The CLI's `embed --scope ... --keep-existing` would also work but
re-embeds the entire scope (wasted ~70 min on the switzerland slice).
This script avoids that.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import Any, Dict, List

from src.index.snsf.config import load_config
from src.index.snsf.document import to_document
from src.index.snsf.embed import embed_passages
from src.index.snsf.embed_pipeline import _payload, _scope_grant_rows
from src.index.snsf.qdrant_store import QdrantSnsfStore
from src.index.snsf.storage.duckdb_store import SnsfStore
from src.v2.canonicalization.snsf import snsf_grant_point_id

LOGGER = logging.getLogger(__name__)


def _existing_point_ids(qstore: QdrantSnsfStore, collection: str) -> set[str]:
    """Scroll the entire collection, collecting point ids (uuid5 of the
    grant URL)."""
    client = qstore.client
    seen: set[str] = set()
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=collection,
            limit=10_000,
            offset=offset,
            with_payload=False,
            with_vectors=False,
        )
        for p in points:
            seen.add(str(p.id))
        if offset is None:
            break
    return seen


async def run(scope_mode: str) -> Dict[str, Any]:
    cfg = load_config()
    if cfg.rcp.token is None:
        msg = "RCP_TOKEN not set."
        raise RuntimeError(msg)

    qstore = QdrantSnsfStore(cfg)
    coll = qstore.collection_name(scope_mode)
    qstore.ensure_collection(scope_mode)

    existing = _existing_point_ids(qstore, coll)
    LOGGER.info("Already in %s: %d point ids", coll, len(existing))

    store = SnsfStore.open()
    try:
        all_rows = _scope_grant_rows(store, scope_mode)
    finally:
        store.close()
    LOGGER.info("Scope %s has %d grants in DuckDB", scope_mode, len(all_rows))

    missing: List[Dict[str, Any]] = [
        r for r in all_rows
        if snsf_grant_point_id(r["grant_number"]) not in existing
    ]
    LOGGER.info("Missing %d grants → embedding", len(missing))

    if not missing:
        return {
            "scope_mode": scope_mode,
            "missing": 0,
            "qdrant_count": qstore.count(scope_mode),
            "qdrant_collection": coll,
        }

    texts = [to_document(r) for r in missing]
    matrix = await embed_passages(cfg.rcp, texts, normalize=True)
    LOGGER.info("Embedded matrix shape=%s", matrix.shape)

    grant_numbers = [r["grant_number"] for r in missing]
    payloads = [_payload(r, t) for r, t in zip(missing, texts)]
    qstore.upsert_records(
        scope_mode,
        grant_numbers=grant_numbers,
        vectors=matrix.tolist(),
        payloads=payloads,
    )

    qcount = qstore.count(scope_mode)
    return {
        "scope_mode": scope_mode,
        "missing": len(missing),
        "embedded": len(missing),
        "qdrant_count": qcount,
        "qdrant_collection": coll,
        "embedding_model": cfg.rcp.embedding_model,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="src.index.snsf.resume_embed")
    parser.add_argument("--scope", default="switzerland")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )

    summary = asyncio.run(run(args.scope))
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
