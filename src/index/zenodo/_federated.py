"""Zenodo registration with the federated discover/hydrate registries.

Zenodo already follows the discover/hydrate pattern via its own CLI
(`discover --source infoscience` + `ingest --ids file`). These wrappers
adapt it to the cross-index protocol so seeds can flow over the
federated `gme discover` / `gme hydrate` pipeline.
"""

from __future__ import annotations

import logging
from typing import Any, Iterator

from src.index._federated.dh_registry import register_discoverer, register_hydrator
from src.index._federated.protocols import (
    HydrationSummary,
    IndexDiscoverer,
    IndexHydrator,
    Seed,
)

LOGGER = logging.getLogger(__name__)

ZENODO_DOI_PREFIX = "10.5281/zenodo."


class ZenodoDiscoverer:
    name = "zenodo"
    accepted_sources = ("infoscience",)

    def discover(self, source: str, **opts: Any) -> Iterator[Seed]:
        if source not in self.accepted_sources:
            message = f"Zenodo: unknown source {source!r}. Accepted: {list(self.accepted_sources)}"
            raise ValueError(message)

        from pathlib import Path
        from src.index.zenodo.ingest.discover import discover_from_infoscience
        from src.index.zenodo.storage.duckdb_store import ZenodoStore

        store = ZenodoStore.open()
        text_dir = opts.get("text_dir")
        result = discover_from_infoscience(
            store=store,
            text_dir=Path(text_dir) if text_dir else None,
        )
        LOGGER.info(
            "zenodo discover (infoscience): scanned=%d new_ids=%d",
            result.files_scanned, len(result.new_ids),
        )
        for zid in result.new_ids:
            yield Seed(
                id=zid,
                seed_type="zenodo_id",
                source="infoscience",
                hint={"citing_infoscience_uuids": result.file_to_rec.get(zid, [])[:3]},
            )


class ZenodoHydrator:
    name = "zenodo"
    accepted_seed_types = ("zenodo_id", "doi")

    def hydrate(
        self,
        seeds,
        *,
        only_unfetched: bool = True,
    ) -> HydrationSummary:
        from src.index.zenodo.config import load_config
        from src.index.zenodo.ingest.records import ingest_by_ids
        from src.index.zenodo.storage.duckdb_store import ZenodoStore

        ids: list[str] = []
        for s in seeds:
            if s.seed_type == "zenodo_id":
                ids.append(s.id)
            elif s.seed_type == "doi":
                # Only Zenodo DOIs (10.5281/zenodo.<id>) are within scope here.
                normalised = s.id.lower().replace("https://doi.org/", "").rstrip("/")
                if normalised.startswith(ZENODO_DOI_PREFIX):
                    ids.append(normalised[len(ZENODO_DOI_PREFIX):])

        if not ids:
            return HydrationSummary()

        config = load_config()
        store = ZenodoStore.open()
        result = ingest_by_ids(
            config=config, store=store, ids=ids, refresh=not only_unfetched,
        )
        return HydrationSummary(
            fetched=result.get("fetched", 0),
            in_scope=result.get("persisted", 0),
            skipped_existing=result.get("skipped", 0),
            errors=result.get("errors", 0),
        )


DISCOVERER = ZenodoDiscoverer()
HYDRATOR = ZenodoHydrator()

register_discoverer(DISCOVERER)
register_hydrator(HYDRATOR)


__all__ = ["DISCOVERER", "HYDRATOR", "ZenodoDiscoverer", "ZenodoHydrator"]
