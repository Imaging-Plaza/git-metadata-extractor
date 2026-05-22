"""Build the communities DuckDB index from a parent-org config file."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

from src.index.communities.ingest.zenodo import discover_by_query, fetch_by_slug
from src.index.communities.storage.duckdb_store import CommunitiesStore

logger = logging.getLogger(__name__)


DEFAULT_CONFIG = Path("config/index/communities.yaml")


def _load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def build_from_config(
    *,
    config_path: Path = DEFAULT_CONFIG,
    store: CommunitiesStore | None = None,
    include_discovery: bool = True,
) -> dict[str, int]:
    """Ingest every community listed (or matched) by the config.

    Returns a `{parent_org: count}` summary the CLI can pretty-print.
    """

    cfg = _load_config(config_path)
    parents = cfg.get("parents") if isinstance(cfg.get("parents"), dict) else {}
    if not parents:
        logger.warning("communities.build: no parents in config %s", config_path)
        return {}

    store = store or CommunitiesStore.open()
    store.bootstrap()

    summary: dict[str, int] = {}
    for parent_org, block in parents.items():
        if not isinstance(block, dict):
            continue
        slugs: list[str] = list(block.get("hardcoded_slugs") or [])
        rows_for_parent: dict[str, dict[str, Any]] = {}
        # 1. Direct slug lookups
        for slug in slugs:
            record = fetch_by_slug(slug, parent_org=parent_org)
            if record:
                rows_for_parent[record["community_id"]] = record
        # 2. Auto-discovery
        if include_discovery:
            # Zenodo's `?q=` is fuzzy: "CERN" matches CERMN/CERTH/CERIC.
            # Filter discovered hits against an optional regex applied
            # to title+description. Hardcoded slugs bypass the filter.
            check_pattern = None
            check_src = block.get("affiliation_check_regex")
            if isinstance(check_src, str) and check_src.strip():
                check_pattern = re.compile(check_src, re.IGNORECASE)
            dropped = 0
            for keyword in (block.get("discovery_queries") or []):
                for record in discover_by_query(keyword, parent_org=parent_org):
                    if check_pattern is not None:
                        haystack = " ".join(
                            (record.get("title") or "", record.get("description") or ""),
                        )
                        if not check_pattern.search(haystack):
                            dropped += 1
                            continue
                    rows_for_parent.setdefault(record["community_id"], record)
            if check_pattern is not None and dropped:
                logger.info(
                    "communities.build: dropped %d fuzzy-match false-positives for parent=%s",
                    dropped, parent_org,
                )
        ok = store.upsert_many(list(rows_for_parent.values()))
        summary[parent_org] = ok
        logger.info(
            "communities.build: ingested %d communities for parent=%s",
            ok, parent_org,
        )

    return summary
