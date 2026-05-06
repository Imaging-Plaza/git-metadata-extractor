"""SNSF registration with the federated discover/hydrate registries.

v1 stub. SNSF is a small index — just registering the surface so it
shows up in ``gme indices``. Real discover/hydrate to follow.
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


class SNSFDiscoverer:
    name = "snsf"
    accepted_sources: tuple[str, ...] = ()

    def discover(self, source: str, **opts: Any) -> Iterator[Seed]:
        message = "SNSF discover not yet implemented"
        raise ValueError(message)


class SNSFHydrator:
    name = "snsf"
    accepted_seed_types: tuple[str, ...] = ()

    def hydrate(self, seeds, *, only_unfetched: bool = True) -> HydrationSummary:
        return HydrationSummary()


register_discoverer(SNSFDiscoverer())
register_hydrator(SNSFHydrator())
