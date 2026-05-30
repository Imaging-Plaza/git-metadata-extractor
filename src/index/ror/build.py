"""Build orchestrator: download → filter → document → embed → store.

Reads the resolved `RorIndexConfig`, fetches the latest dump (or reuses a
cached one), filters to the configured subset, embeds all docs via RCP, and
writes:

  - the **full ROR dump** (~125k records) into the DuckDB `records` table
    (replaces what was there from the previous release);
  - this run's scope rows (text + Qdrant point id) into `scope_records`;
  - a manifest entry for this scope into `manifests`;
  - and the embedded vectors into the matching Qdrant collection
    `ror_<scope_mode>` (recreated each build).

D16 (see `.internal/ror/duckdb-migration.md`) collapsed the per-scope
`records.jsonl` + `manifest.json` sidecars and the in-memory `dump_index`
into one DuckDB file at `<INDEX_DATA_DIR>/ror/duckdb/ror.duckdb`. The Qdrant
collection layout is unchanged.
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
from .qdrant_store import QdrantRorStore
from .storage.duckdb_store import (
    RorStore,
    ScopeRecord,
    StoreManifest,
    extract_record_columns,
    vector_id_for,
)

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

    # ---- DuckDB writes (records + scope_records + manifests) ------------
    duck = RorStore.open()
    try:
        duck_records_count = duck.bulk_replace_records(
            extract_record_columns(r, ror_release_version=cached.release_version)
            for r in all_records
        )
        logger.info(
            "DuckDB: replaced `records` with %d rows (release=%s)",
            duck_records_count, cached.release_version,
        )

        scope_rows = [
            ScopeRecord(
                scope_mode=cfg.scope.mode,
                ror_id=str(record.get("id", "")).rstrip("/"),
                text=text,
                vector_id=vector_id_for(str(record.get("id", "")).rstrip("/")),
            )
            for record, text in zip(subset, texts)
        ]
        duck.set_scope_records(cfg.scope.mode, scope_rows)
        duck.set_manifest(StoreManifest(
            scope_mode=cfg.scope.mode,
            record_count=len(scope_rows),
            embedding_model=cfg.rcp.embedding_model,
            embedding_dim=cfg.rcp.embedding_dim,
            reranker_model=cfg.rcp.reranker_model,
            ror_release_version=cached.release_version,
            ror_release_doi=cached.release_doi,
        ))
    finally:
        duck.close()

    # ---- Qdrant writes (vectors + payload) -------------------------------
    qstore = QdrantRorStore(cfg)
    qstore.recreate_collection(cfg.scope.mode)
    payloads = [_build_payload(record, text) for record, text in zip(subset, texts)]
    qstore.upsert_records(
        cfg.scope.mode,
        ror_ids=[scope_rows[i].ror_id for i in range(len(scope_rows))],
        vectors=embeddings.tolist(),
        payloads=payloads,
    )
    logger.info(
        "Upserted %d records to qdrant collection %s",
        len(scope_rows), qstore.collection_name(cfg.scope.mode),
    )

    return {
        "scope_mode": cfg.scope.mode,
        "record_count": len(scope_rows),
        "release_version": cached.release_version,
        "json_path": str(cached.json_path),
        "qdrant_collection": qstore.collection_name(cfg.scope.mode),
        "duckdb_records": duck_records_count,
    }


def _build_payload(record: Dict[str, Any], text: str) -> Dict[str, Any]:
    """Qdrant payload for one scope row: kept compact but searchable."""
    cc: Optional[str] = None
    for loc in record.get("locations") or []:
        details = loc.get("geonames_details") if isinstance(loc, dict) else None
        if isinstance(details, dict):
            value = details.get("country_code")
            if isinstance(value, str) and value:
                cc = value.upper()
                break
    return {
        "ror_id": str(record.get("id", "")).rstrip("/"),
        "name": display_name(record),
        "text": text,
        "country_code": cc,
        "types": record.get("types") or [],
        "record": record,
    }


def run(cfg: RorIndexConfig, **kwargs) -> Dict[str, Any]:
    """Sync wrapper for the CLI."""
    return asyncio.run(build(cfg, **kwargs))
