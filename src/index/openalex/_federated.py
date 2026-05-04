"""OpenAlex registration with the federated discover/hydrate registries.

Imported lazily by ``src.index._federated.dh_registry.load_*``.
"""

from __future__ import annotations

from src.index._federated.dh_registry import register_discoverer, register_hydrator
from src.index.openalex.discover import DISCOVERER
from src.index.openalex.hydrate import HYDRATOR

register_discoverer(DISCOVERER)
register_hydrator(HYDRATOR)
