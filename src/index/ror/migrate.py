"""One-shot migration from legacy FAISS-on-disk indexes to Qdrant.

For each scope on disk (`<data_dir>/index/<scope>/`) that has a legacy
`index.faiss` file, this module reads the vectors back via faiss
`reconstruct_n`, joins them with the row-aligned `records.jsonl`, and upserts
into the matching Qdrant collection. No RCP calls — vectors are reused.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np

from .build import _build_payload  # noqa: PLC2701 — internal reuse
from .config import RorIndexConfig
from .paths import index_data_root
from .qdrant_store import QdrantRorStore
from .store import has_legacy_faiss, read_legacy_faiss, read_records

logger = logging.getLogger(__name__)


def list_scope_dirs() -> List[str]:
    base = index_data_root() / "ror" / "index"
    if not base.exists():
        return []
    return sorted([p.name for p in base.iterdir() if p.is_dir()])


def migrate_scope(cfg: RorIndexConfig, scope_mode: str) -> Dict[str, Any]:
    """Migrate one scope's vectors from FAISS to Qdrant."""
    if not has_legacy_faiss(scope_mode):
        msg = (
            f"No legacy FAISS file for scope {scope_mode!r}; nothing to migrate. "
            f"Run `python -m src.index.ror build` to create the Qdrant collection."
        )
        raise FileNotFoundError(msg)

    rows = read_records(scope_mode)
    index = read_legacy_faiss(scope_mode)
    if index.ntotal != len(rows):
        msg = (
            f"FAISS row count ({index.ntotal}) differs from records.jsonl "
            f"length ({len(rows)}) for scope {scope_mode!r}. Aborting."
        )
        raise ValueError(msg)

    vectors_np = index.reconstruct_n(0, index.ntotal).astype(np.float32, copy=False)
    if vectors_np.shape[1] != cfg.rcp.embedding_dim:
        msg = (
            f"FAISS dim {vectors_np.shape[1]} != config dim {cfg.rcp.embedding_dim} "
            f"for scope {scope_mode!r}. Re-embed instead of migrating."
        )
        raise ValueError(msg)

    store = QdrantRorStore(cfg)
    store.recreate_collection(scope_mode)
    payloads = [_build_payload(row.record, row.text) for row in rows]
    store.upsert_records(
        scope_mode,
        ror_ids=[row.ror_id for row in rows],
        vectors=vectors_np.tolist(),
        payloads=payloads,
    )
    qcount = store.count(scope_mode)
    logger.info(
        "Migrated scope=%s rows=%d → qdrant=%d", scope_mode, len(rows), qcount,
    )
    return {
        "scope_mode": scope_mode,
        "rows": len(rows),
        "qdrant_count": qcount,
        "qdrant_collection": store.collection_name(scope_mode),
    }


def migrate_all(cfg: RorIndexConfig) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for scope in list_scope_dirs():
        if not has_legacy_faiss(scope):
            continue
        out.append(migrate_scope(cfg, scope))
    return out


__all__ = ["list_scope_dirs", "migrate_all", "migrate_scope"]
