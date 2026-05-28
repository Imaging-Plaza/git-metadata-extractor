"""Infoscience registration with the federated discover/hydrate registries.

Thin wrapper. The underlying ``infoscience`` module already supports
``discover`` (terms-based async search, persists state to disk) and
``ingest-duckdb`` (build the DuckDB store from discovered links). v1
exposes the search-driven discover path; hydrate by UUID/handle is a
TODO (current ingest is bulk-only).
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


class InfoscienceDiscoverer:
    name = "infoscience"
    accepted_sources = ("from-search",)

    def discover(self, source: str, **opts: Any) -> Iterator[Seed]:
        if source not in self.accepted_sources:
            message = f"Infoscience: unknown source {source!r}. Accepted: {list(self.accepted_sources)}"
            raise ValueError(message)

        # Re-use the existing async discover entrypoint. Returns Infoscience
        # UUIDs / handle URLs the user could feed back into hydrate later.
        # For v1 we yield seeds based on the existing on-disk state file
        # so that re-discoveries are cached.
        from src.index.infoscience.discover import discover_state_path

        state_path = discover_state_path()
        if not state_path.exists():
            LOGGER.warning(
                "infoscience discover state %s not found; "
                "run `python -m src.index.infoscience discover --terms ...` first",
                state_path,
            )
            return
        # Yield each link captured in the state file as a seed.
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
                seed_type="infoscience_url",
                source="from-search",
                hint={"query": opts.get("query")},
            )


class InfoscienceHydrator:
    name = "infoscience"
    accepted_seed_types = ("infoscience_url",)

    def hydrate(self, seeds, *, only_unfetched: bool = True) -> HydrationSummary:
        # TODO: lift the existing build-from-links code into a callable
        # `hydrate_from_urls(urls)` so we can route Seeds here. v1 returns
        # a no-op summary.
        materialised = list(seeds)
        LOGGER.warning(
            "infoscience: hydrate is a stub (received %d seeds). "
            "Use `python -m src.index.infoscience ingest-duckdb` for now.",
            len(materialised),
        )
        return HydrationSummary(skipped_existing=len(materialised))


register_discoverer(InfoscienceDiscoverer())
register_hydrator(InfoscienceHydrator())
