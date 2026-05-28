"""ORCID registration with the federated discover/hydrate registries.

ORCID is the canonical model for the discover/hydrate pattern (see
``.internal/federated/discover-hydrate-design.md``):

- ``discover`` populates the ``seeds`` DuckDB table with ORCID IDs to
  fetch, plus the ``discovered_via`` provenance flag.
- ``ingest`` (now wrapped here as ``hydrate``) streams from
  ``seeds.LEFT JOIN persons`` filtered by ``persons.orcid_id IS NULL``,
  fetches each, and persists.

The wrappers are thin facades over
``src.index.orcid.ingest.{discover,persons}`` — no behavioural changes
to the underlying ingest path.
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


class ORCIDDiscoverer:
    name = "orcid"
    accepted_sources = ("openalex", "orcid_search", "both")

    def discover(self, source: str, **opts: Any) -> Iterator[Seed]:
        if source not in self.accepted_sources:
            message = f"ORCID: unknown source {source!r}. Accepted: {list(self.accepted_sources)}"
            raise ValueError(message)

        from src.index.orcid.config import load_config
        from src.index.orcid.ingest.discover import discover_seeds
        from src.index.orcid.storage.duckdb_store import OrcidDuckDBStore

        scope = opts.get("scope", "switzerland")
        config = load_config(scope=scope)
        store = OrcidDuckDBStore.open(scope=scope)

        # Run discover_seeds — populates the seeds table.
        summary = discover_seeds(config=config, store=store, source=source)
        LOGGER.info("orcid discover (scope=%s, source=%s): %s", scope, source, summary)

        # Yield seeds that haven't been hydrated yet (LEFT JOIN persons).
        # Filter by `discovered_via` prefix when source != 'both'.
        cur = store.connect()
        if source == "both":
            sql = (
                "SELECT s.orcid_id, s.discovered_via, s.hint "
                "FROM seeds s LEFT JOIN persons p ON p.orcid_id = s.orcid_id "
                "WHERE p.orcid_id IS NULL ORDER BY s.discovered_at"
            )
            rows = cur.execute(sql).fetchall()
        else:
            sql = (
                "SELECT s.orcid_id, s.discovered_via, s.hint "
                "FROM seeds s LEFT JOIN persons p ON p.orcid_id = s.orcid_id "
                "WHERE p.orcid_id IS NULL AND "
                "(s.discovered_via = ? OR s.discovered_via = 'both') "
                "ORDER BY s.discovered_at"
            )
            rows = cur.execute(sql, [source]).fetchall()
        for orcid_id, discovered_via, hint in rows:
            yield Seed(
                id=orcid_id,
                seed_type="orcid",
                source=f"orcid:{discovered_via}",
                hint={"scope": scope, "discovered_via": discovered_via, "label": hint},
            )


class ORCIDHydrator:
    name = "orcid"
    accepted_seed_types = ("orcid",)

    def hydrate(
        self,
        seeds,
        *,
        only_unfetched: bool = True,
    ) -> HydrationSummary:
        from src.index.orcid.config import load_config
        from src.index.orcid.ingest.persons import ingest_persons
        from src.index.orcid.storage.duckdb_store import OrcidDuckDBStore

        seed_list = [s for s in seeds if s.seed_type == "orcid"]
        if not seed_list:
            return HydrationSummary()

        # All seeds in one batch share scope (assumed). If callers mix scopes
        # they should hydrate twice.
        scope = (seed_list[0].hint or {}).get("scope") or "switzerland"
        config = load_config(scope=scope)
        store = OrcidDuckDBStore.open(scope=scope)

        # Upsert each seed into the seeds table so ingest_persons can pick it up.
        # If the seed came from another index's discover, this is the bridge.
        # Idempotent — `upsert_seed` does a sensible merge on conflict.
        for seed in seed_list:
            store.upsert_seed(
                orcid_id=seed.id,
                discovered_via=(seed.hint or {}).get("discovered_via", "external"),
                hint=(seed.hint or {}).get("label"),
            )

        # `ingest_persons` already filters via stream_seeds(only_unfetched=True);
        # if the caller wants to force re-fetch we don't currently support it
        # without touching the underlying API. Match ORCID's semantics.
        ingest_summary = ingest_persons(
            config=config, store=store, scope=scope,
            limit=None, priority_hints=None,
        )
        out = HydrationSummary(
            fetched=ingest_summary.get("fetched", 0),
            in_scope=ingest_summary.get("in_scope", 0),
            out_of_scope=ingest_summary.get("out_of_scope", 0),
            errors=ingest_summary.get("errors", 0),
        )
        if not only_unfetched:
            out.extras["only_unfetched_ignored"] = True
        return out


DISCOVERER = ORCIDDiscoverer()
HYDRATOR = ORCIDHydrator()

register_discoverer(DISCOVERER)
register_hydrator(HYDRATOR)


__all__ = ["DISCOVERER", "HYDRATOR", "ORCIDDiscoverer", "ORCIDHydrator"]
