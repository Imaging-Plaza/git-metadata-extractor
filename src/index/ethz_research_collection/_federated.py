"""ETH Research Collection registration with the federated discover/hydrate registries.

Mirrors :mod:`src.index.infoscience._federated` — both indices are
DSpace-backed and share the discover/build pipeline shape.
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


class ETHZResearchCollectionDiscoverer:
    name = "ethz_research_collection"
    accepted_sources = ("from-search",)

    def discover(self, source: str, **opts: Any) -> Iterator[Seed]:
        if source not in self.accepted_sources:
            message = f"ETHZ-RC: unknown source {source!r}. Accepted: {list(self.accepted_sources)}"
            raise ValueError(message)

        from src.index.ethz_research_collection.discover import discover_state_path

        state_path = discover_state_path()
        if not state_path.exists():
            LOGGER.warning(
                "ethz_research_collection discover state %s not found; "
                "run `python -m src.index.ethz_research_collection discover --terms ...` first",
                state_path,
            )
            return
        import json
        try:
            data = json.loads(state_path.read_text())
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("failed to read %s: %s", state_path, exc)
            return
        items = data.get("links") or data.get("items") or []
        for item in items:
            url = item if isinstance(item, str) else item.get("url")
            if not url:
                continue
            yield Seed(
                id=url,
                seed_type="ethz_rc_url",
                source="from-search",
                hint={"query": opts.get("query")},
            )


class ETHZResearchCollectionHydrator:
    name = "ethz_research_collection"
    accepted_seed_types = ("ethz_rc_url",)

    def hydrate(self, seeds, *, only_unfetched: bool = True) -> HydrationSummary:
        # TODO: lift the build-from-links code into hydrate_from_urls(urls).
        materialised = list(seeds)
        LOGGER.warning(
            "ethz_research_collection: hydrate is a stub (received %d seeds). "
            "Use `python -m src.index.ethz_research_collection ingest-duckdb` for now.",
            len(materialised),
        )
        return HydrationSummary(skipped_existing=len(materialised))


register_discoverer(ETHZResearchCollectionDiscoverer())
register_hydrator(ETHZResearchCollectionHydrator())
