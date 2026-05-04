"""RenkuLab registration with the federated discover/hydrate registries.

Hydrate seed types
------------------

- ``renkulab_url`` — RenkuLab project / group / user / data_connector URLs.
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


class RenkuLabDiscoverer:
    name = "renkulab"
    accepted_sources = ("from-search",)

    def discover(self, source: str, **opts: Any) -> Iterator[Seed]:
        if source not in self.accepted_sources:
            message = f"RenkuLab: unknown source {source!r}. Accepted: {list(self.accepted_sources)}"
            raise ValueError(message)
        # Placeholder: wrap the existing search-based ingest discover in a follow-up.
        LOGGER.warning("renkulab discover is a stub")
        return


class RenkuLabHydrator:
    name = "renkulab"
    accepted_seed_types = ("renkulab_url",)

    def hydrate(self, seeds, *, only_unfetched: bool = True) -> HydrationSummary:
        materialised = list(seeds)
        LOGGER.warning(
            "renkulab: hydrate is a stub (received %d seeds).", len(materialised),
        )
        return HydrationSummary(skipped_existing=len(materialised))


register_discoverer(RenkuLabDiscoverer())
register_hydrator(RenkuLabHydrator())
