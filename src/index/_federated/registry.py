"""Adapter ABC + global registry.

Each index module registers an adapter at import time via `register(adapter)`.
The registry is the single source of truth for "which indices are available".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class Hit:
    """A cross-index search hit. Index- and adapter-agnostic."""

    index: str                # short module name, e.g. 'huggingface'
    entity_type: str          # type within that index, e.g. 'model' / 'work' / 'person'
    id: str                   # canonical id within the index
    title: str | None         # human-readable label for display
    score: float              # primary score (rerank if available, else vector)
    summary: str | None = None  # short text snippet (often the rerank doc)
    url: str | None = None    # canonical URL on the source site, when known
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class EntityRecord:
    """A canonical record returned by `adapter.lookup()`."""

    index: str
    entity_type: str
    id: str
    data: dict[str, Any] = field(default_factory=dict)
    url: str | None = None


@runtime_checkable
class IndexAdapter(Protocol):
    """Minimal contract every index adapter must satisfy.

    Adapters live under `src/index/_federated/adapters/<name>.py` and are
    expected to be cheap to import. Heavy imports (RCP clients, qdrant,
    duckdb) should happen inside the methods, not at module top-level.
    """

    name: str
    entity_types: list[str]

    def search(
        self,
        *,
        query: str,
        entity_type: str | None,
        top_k: int,
        filters: dict[str, Any] | None,
    ) -> list[Hit]:
        """Return up to `top_k` hits. `entity_type=None` → adapter chooses default(s)."""
        ...

    def lookup(self, identifier: str) -> list[EntityRecord]:
        """Resolve `identifier` (slug / orcid / doi / ror / uuid / URL) to records.

        Adapter inspects the string and returns 0+ matches. Empty list means
        "this identifier doesn't apply to my index".
        """
        ...


REGISTRY: dict[str, IndexAdapter] = {}


def register(adapter: IndexAdapter) -> None:
    """Register an adapter. Called at import time from each `adapters/<name>.py`."""
    if not isinstance(adapter, IndexAdapter):
        message = f"{adapter!r} does not satisfy IndexAdapter"
        raise TypeError(message)
    REGISTRY[adapter.name] = adapter


def load_adapters(only: list[str] | None = None) -> list[IndexAdapter]:
    """Import the adapter modules so they self-register, then return the active set."""
    # Imports are inside the function so `gme indices` doesn't pay the cost
    # for indices the user isn't asking about.
    from importlib import import_module

    candidates = [
        "huggingface", "openalex", "infoscience", "orcid", "ror", "zenodo",
        "ethz_research_collection", "github", "snsf", "renkulab", "epfl_graph",
        "swissubase", "zenodo_communities",
    ]
    targets = [c for c in candidates if (only is None or c in only)]
    for name in targets:
        try:
            import_module(f"src.index._federated.adapters.{name}")
        except Exception as exc:  # noqa: BLE001 — adapter import failure shouldn't kill the whole CLI
            import logging
            logging.getLogger(__name__).warning(
                "federated: adapter %r failed to import (%s); skipping",
                name, exc,
            )
    return [REGISTRY[n] for n in targets if n in REGISTRY]
