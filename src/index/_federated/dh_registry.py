"""Registries for the discover/hydrate protocols.

Sibling to ``registry.py``. Kept in its own module so the
``IndexAdapter`` (search/lookup) registry stays decoupled — adapters
that don't yet implement discover/hydrate can still register for
search.

See :mod:`src.index._federated.protocols` for the protocol definitions
and ``.internal/federated/discover-hydrate-design.md`` for the design
rationale.
"""

from __future__ import annotations

import logging
from importlib import import_module
from typing import Iterable

from src.index._federated.protocols import (
    HydrationSummary,
    IndexDiscoverer,
    IndexHydrator,
    Seed,
)

LOGGER = logging.getLogger(__name__)


DISCOVERERS: dict[str, IndexDiscoverer] = {}
HYDRATORS: dict[str, IndexHydrator] = {}


def register_discoverer(discoverer: IndexDiscoverer) -> None:
    """Register a discoverer. Call from each index's ``_federated.py`` at import time."""
    if not isinstance(discoverer, IndexDiscoverer):
        message = f"{discoverer!r} does not satisfy IndexDiscoverer"
        raise TypeError(message)
    DISCOVERERS[discoverer.name] = discoverer


def register_hydrator(hydrator: IndexHydrator) -> None:
    """Register a hydrator. Call from each index's ``_federated.py`` at import time."""
    if not isinstance(hydrator, IndexHydrator):
        message = f"{hydrator!r} does not satisfy IndexHydrator"
        raise TypeError(message)
    HYDRATORS[hydrator.name] = hydrator


# Same candidates list as registry.py so we share the index roster.
# Each module's `_federated.py` is responsible for importing both
# discoverer + hydrator classes and calling the register_* helpers.
_CANDIDATES = [
    "openalex",
    "orcid",
    "zenodo",
    "infoscience",
    "ethz_research_collection",
    "github",
    "huggingface",
    "snsf",
    "renkulab",
    "swissubase",
    # ROR + EPFL Graph are dump-driven — no discover/hydrate.
]


def _load(target: str, kind: str) -> None:
    """Import ``src.index.<target>._federated`` to trigger self-registration.

    Failures are logged but never raised: a single broken module shouldn't
    take down the whole CLI.
    """
    try:
        import_module(f"src.index.{target}._federated")
    except ModuleNotFoundError:
        # Index hasn't been migrated to the new protocols yet. That's fine.
        LOGGER.debug("federated/%s: %s has no _federated module yet", kind, target)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning(
            "federated/%s: %s failed to import (%s); skipping", kind, target, exc,
        )


def load_discoverers(only: list[str] | None = None) -> list[IndexDiscoverer]:
    """Trigger self-registration for the requested set, return active discoverers.

    ``only`` filters the result by name. If ``only`` is ``None`` we load every
    candidate and return everything in the registry (including discoverers
    that registered themselves outside the candidate list, e.g. tests).
    """
    targets_to_load = _CANDIDATES if only is None else [c for c in _CANDIDATES if c in only]
    for name in targets_to_load:
        _load(name, "discoverer")
    if only is None:
        return list(DISCOVERERS.values())
    return [DISCOVERERS[n] for n in only if n in DISCOVERERS]


def load_hydrators(only: list[str] | None = None) -> list[IndexHydrator]:
    """Trigger self-registration for the requested set, return active hydrators."""
    targets_to_load = _CANDIDATES if only is None else [c for c in _CANDIDATES if c in only]
    for name in targets_to_load:
        _load(name, "hydrator")
    if only is None:
        return list(HYDRATORS.values())
    return [HYDRATORS[n] for n in only if n in HYDRATORS]


def dispatch_hydrate(
    seeds: Iterable[Seed],
    *,
    only_unfetched: bool = True,
    only: list[str] | None = None,
) -> dict[str, HydrationSummary]:
    """Group ``seeds`` by ``seed_type``, route each group to the matching hydrator(s).

    A single ``seed_type`` may be accepted by multiple hydrators (e.g.
    a DOI seed could be hydrated by both ``openalex`` and ``zenodo`` from
    their own perspectives). All matching hydrators get the same group.

    Returns a per-hydrator summary dict.
    """
    hydrators = load_hydrators(only=only)
    if not hydrators:
        return {}

    materialised = list(seeds)
    by_type: dict[str, list[Seed]] = {}
    for s in materialised:
        by_type.setdefault(s.seed_type, []).append(s)

    summaries: dict[str, HydrationSummary] = {}
    for hyd in hydrators:
        accepted = set(hyd.accepted_seed_types)
        relevant: list[Seed] = []
        for st, batch in by_type.items():
            if st in accepted:
                relevant.extend(batch)
        if not relevant:
            continue
        try:
            summary = hyd.hydrate(relevant, only_unfetched=only_unfetched)
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("hydrator %r failed: %s", hyd.name, exc)
            summary = HydrationSummary(errors=len(relevant))
        summaries[hyd.name] = summary
    return summaries


__all__ = [
    "DISCOVERERS",
    "HYDRATORS",
    "register_discoverer",
    "register_hydrator",
    "load_discoverers",
    "load_hydrators",
    "dispatch_hydrate",
]
