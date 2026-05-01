"""Sidecar files for the ROR index.

Vector storage now lives in Qdrant (see `qdrant_store.py`). This module only
manages the on-disk artifacts that travel alongside the vectors:

  - `records.jsonl` — one JSON object per row, keyed by `row` index. Carries
    the full ROR record so we can return it from queries without a Qdrant
    payload round-trip when the caller already has the row index.
  - `manifest.json` — release / model / count metadata for the build.

The legacy `index.faiss` file is still readable here via `read_legacy_faiss()`
to support a one-time migration from FAISS-built indexes to Qdrant. New builds
do not write a `.faiss` file.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import List, Optional

from .models import IndexedRecord, IndexManifest
from .paths import faiss_path, manifest_path, records_path

logger = logging.getLogger(__name__)


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def write_sidecar(
    scope_mode: str,
    rows: List[IndexedRecord],
    manifest: IndexManifest,
) -> None:
    """Persist `records.jsonl` + `manifest.json` atomically (per-file)."""
    rp = records_path(scope_mode)
    mp = manifest_path(scope_mode)

    rp_tmp = rp.with_suffix(rp.suffix + ".part")
    mp_tmp = mp.with_suffix(mp.suffix + ".part")

    with rp_tmp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row.model_dump(), ensure_ascii=False) + "\n")
    mp_tmp.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

    rp_tmp.replace(rp)
    mp_tmp.replace(mp)
    logger.info("Wrote ROR sidecar scope=%s rows=%d to %s", scope_mode, len(rows), rp.parent)


def read_records(scope_mode: str) -> List[IndexedRecord]:
    rp = records_path(scope_mode)
    if not rp.exists():
        msg = f"Records file not found: {rp}. Run `build` first."
        raise FileNotFoundError(msg)
    out: List[IndexedRecord] = []
    with rp.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(IndexedRecord(**json.loads(line)))
    return out


def read_manifest(scope_mode: str) -> IndexManifest:
    mp = manifest_path(scope_mode)
    if not mp.exists():
        msg = f"Manifest not found: {mp}. Run `build` first."
        raise FileNotFoundError(msg)
    return IndexManifest(**json.loads(mp.read_text(encoding="utf-8")))


def has_legacy_faiss(scope_mode: str) -> bool:
    return faiss_path(scope_mode).exists()


def read_legacy_faiss(scope_mode: str):
    """Open a legacy FAISS index for migration. Lazily imports faiss-cpu."""
    fp = faiss_path(scope_mode)
    if not fp.exists():
        msg = f"Legacy FAISS index not found: {fp}"
        raise FileNotFoundError(msg)
    try:
        import faiss
    except ImportError as exc:
        msg = (
            "faiss-cpu is required to migrate legacy ROR indexes. "
            "Install with `pip install faiss-cpu`."
        )
        raise RuntimeError(msg) from exc
    return faiss.read_index(str(fp))


__all__: List[str] = [
    "has_legacy_faiss",
    "now_iso",
    "read_legacy_faiss",
    "read_manifest",
    "read_records",
    "write_sidecar",
]
