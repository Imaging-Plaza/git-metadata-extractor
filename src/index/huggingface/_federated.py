"""HuggingFace registration with the federated discover/hydrate registries.

Discover sources
----------------

- ``orgs`` — discover model/dataset/space owners via the HF Hub search.
- ``from-search`` — generic full-text search yielding model/dataset/space IDs.

Hydrate seed types
------------------

- ``hf_model`` / ``hf_dataset`` / ``hf_space`` / ``hf_org`` — bulk fetch by ID.

v1 only registers the Discoverer + Hydrator surface; the ingest helpers
are wrapped in follow-up work.
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


class HuggingFaceDiscoverer:
    name = "huggingface"
    accepted_sources = ("orgs", "from-search")

    def discover(self, source: str, **opts: Any) -> Iterator[Seed]:
        if source not in self.accepted_sources:
            message = f"HF: unknown source {source!r}. Accepted: {list(self.accepted_sources)}"
            raise ValueError(message)

        if source == "orgs":
            try:
                from src.index.huggingface.ingest.orgs_ingest import discover_orgs
            except ImportError:
                LOGGER.warning("hf discover_orgs not importable")
                return
            query = opts.get("query") or ""
            for org in discover_orgs(query=query):  # type: ignore[call-arg]
                yield Seed(
                    id=org if isinstance(org, str) else org.get("id"),
                    seed_type="hf_org",
                    source="orgs",
                    hint={"query": query},
                )
            return

        # source == "from-search" — placeholder until we wrap a search call
        LOGGER.warning("hf from-search discover is a stub")
        return


class HuggingFaceHydrator:
    name = "huggingface"
    accepted_seed_types = ("hf_model", "hf_dataset", "hf_space", "hf_org")

    def hydrate(self, seeds, *, only_unfetched: bool = True) -> HydrationSummary:
        # TODO: route by seed_type to ingest_models / ingest_datasets / ingest_spaces / ingest_orgs.
        materialised = list(seeds)
        LOGGER.warning(
            "huggingface: hydrate is a stub (received %d seeds).",
            len(materialised),
        )
        return HydrationSummary(skipped_existing=len(materialised))


register_discoverer(HuggingFaceDiscoverer())
register_hydrator(HuggingFaceHydrator())
