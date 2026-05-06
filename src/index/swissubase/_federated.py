"""SWISSUbase registration with the federated discover/hydrate registries.

Hydrate seed types
------------------

- ``swissubase_url`` — SWISSUbase study / dataset URLs (Selenium-driven).

v1 stub. Selenium adds friction; the real hydrator will batch via the
existing ``ingest_studies`` driver.
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


class SwissubaseDiscoverer:
    name = "swissubase"
    accepted_sources = ("from-search",)

    def discover(self, source: str, **opts: Any) -> Iterator[Seed]:
        if source not in self.accepted_sources:
            message = f"Swissubase: unknown source {source!r}. Accepted: {list(self.accepted_sources)}"
            raise ValueError(message)
        LOGGER.warning("swissubase discover is a stub")
        return


class SwissubaseHydrator:
    name = "swissubase"
    accepted_seed_types = ("swissubase_url",)

    def hydrate(self, seeds, *, only_unfetched: bool = True) -> HydrationSummary:
        materialised = list(seeds)
        LOGGER.warning(
            "swissubase: hydrate is a stub (received %d seeds).", len(materialised),
        )
        return HydrationSummary(skipped_existing=len(materialised))


register_discoverer(SwissubaseDiscoverer())
register_hydrator(SwissubaseHydrator())
