"""Legacy sidecar readers for the ROR index.

After D16 (see `.internal/ror/duckdb-migration.md`), all writes go through
`storage.duckdb_store.RorStore`. This module survives only to support:

  - reading existing `records.jsonl` / `manifest.json` files left on disk
    by pre-D16 builds (used by the `migrate-storage` porter and the
    one-shot D15 FAISS→Qdrant migrator),
  - the `read_legacy_faiss()` helper that opens an `index.faiss` file when
    present (D15).

New code should not call `write_sidecar` — it has been removed. New builds
write to DuckDB only.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import List

from .models import IndexedRecord, IndexManifest
from .paths import faiss_path, manifest_path, records_path

logger = logging.getLogger(__name__)


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


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
]
