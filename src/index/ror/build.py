"""Build orchestrator: download → filter → document → embed → store.

Reads the resolved `RorIndexConfig`, fetches the latest dump (or reuses a
cached one), filters to the configured subset, embeds all docs via RCP, and
writes `index.faiss` + `records.jsonl` + `manifest.json` under
`<data_dir>/ror/index/<scope_mode>/`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from .config import RorIndexConfig
from .document import display_name, to_document
from .embed import embed_passages
from .filter import EUROPE_COUNTRY_CODES, filter_countries, filter_country_code, filter_subtree
from .models import IndexedRecord, IndexManifest
from .qdrant_store import QdrantRorStore
from .store import now_iso, write_sidecar

logger = logging.getLogger(__name__)


def _load_dump(json_path) -> List[Dict[str, Any]]:
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        msg = f"Expected ROR dump JSON list at {json_path}, got {type(data).__name__}"
        raise ValueError(msg)
    return data


def _select_subset(
    records: List[Dict[str, Any]],
    cfg: RorIndexConfig,
) -> List[Dict[str, Any]]:
    if cfg.scope.mode == "epfl_ethz":
        return filter_subtree(
            records,
            seeds=cfg.scope.seeds,
            expand_types=cfg.scope.expand,
            max_depth=cfg.scope.max_depth,
        )
    if cfg.scope.mode == "switzerland":
        return filter_country_code(records, "CH")
    if cfg.scope.mode == "europe":
        return filter_countries(records, EUROPE_COUNTRY_CODES)
    if cfg.scope.mode == "worldwide":
        return list(records)
    msg = f"Unknown scope.mode: {cfg.scope.mode}"
    raise ValueError(msg)


async def build(cfg: RorIndexConfig, *, refresh: bool = False) -> Dict[str, Any]:
    """Run the full build pipeline. Returns a summary dict."""
    from .download import fetch_latest_dump  # local import keeps requests optional in tests

    cached = fetch_latest_dump(
        cfg.ror_dump.zenodo_concept_doi,
        refresh=refresh,
    )
    logger.info("Using ROR dump version=%s at %s", cached.release_version, cached.json_path)

    all_records = _load_dump(cached.json_path)
    logger.info("Loaded %d records from dump", len(all_records))

    subset = _select_subset(all_records, cfg)
    logger.info("Subset (mode=%s) has %d records", cfg.scope.mode, len(subset))
    if not subset:
        msg = (
            f"Subset is empty for scope.mode={cfg.scope.mode!r}. "
            f"Check seeds / country filter."
        )
        raise ValueError(msg)

    texts = [to_document(r) for r in subset]
    embeddings = await embed_passages(cfg.rcp, texts, normalize=True)

    rows: List[IndexedRecord] = []
    for i, (record, text) in enumerate(zip(subset, texts)):
        rows.append(IndexedRecord(
            row=i,
            ror_id=str(record.get("id", "")),
            name=display_name(record),
            text=text,
            record=record,
        ))

    manifest = IndexManifest(
        scope_mode=cfg.scope.mode,
        record_count=len(rows),
        embedding_model=cfg.rcp.embedding_model,
        embedding_dim=cfg.rcp.embedding_dim,
        reranker_model=cfg.rcp.reranker_model,
        ror_release_version=cached.release_version,
        ror_release_doi=cached.release_doi,
        built_at_iso=now_iso(),
    )

    # Sidecar (records.jsonl + manifest.json) — keep on disk for portability.
    write_sidecar(cfg.scope.mode, rows, manifest)

    # Vectors → Qdrant collection ror_<scope_mode>.
    store = QdrantRorStore(cfg)
    store.recreate_collection(cfg.scope.mode)
    payloads = [_build_payload(row) for row in rows]
    store.upsert_records(
        cfg.scope.mode,
        ror_ids=[row.ror_id for row in rows],
        vectors=embeddings.tolist(),
        payloads=payloads,
    )
    logger.info(
        "Upserted %d records to qdrant collection %s",
        len(rows), store.collection_name(cfg.scope.mode),
    )

    return {
        "scope_mode": cfg.scope.mode,
        "record_count": len(rows),
        "release_version": cached.release_version,
        "json_path": str(cached.json_path),
        "qdrant_collection": store.collection_name(cfg.scope.mode),
    }


def _build_payload(row: IndexedRecord) -> Dict[str, Any]:
    """Qdrant payload for one row: kept compact but searchable."""
    record = row.record
    cc: Optional[str] = None
    for loc in record.get("locations") or []:
        details = loc.get("geonames_details") if isinstance(loc, dict) else None
        if isinstance(details, dict):
            value = details.get("country_code")
            if isinstance(value, str) and value:
                cc = value.upper()
                break
    return {
        "ror_id": row.ror_id,
        "name": row.name,
        "text": row.text,
        "country_code": cc,
        "types": record.get("types") or [],
        "record": record,
    }


def run(cfg: RorIndexConfig, **kwargs) -> Dict[str, Any]:
    """Sync wrapper for the CLI."""
    return asyncio.run(build(cfg, **kwargs))
