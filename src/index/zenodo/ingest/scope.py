"""Scope filter builder for Zenodo communities.

Phase 1: `epfl` resolves to a curated EPFL community list.
Phase 2: `switzerland` returns the EPFL list ∪ a curated Swiss list (deduped).

We loop the resolved community list at the ingest layer and dedupe records by
`zenodo_id` at upsert time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from src.index.zenodo.config import ZenodoIndexConfig

ScopeName = Literal["epfl", "switzerland"]


@dataclass(slots=True, frozen=True)
class Scope:
    name: str
    communities: tuple[str, ...]


def epfl_scope(config: ZenodoIndexConfig) -> Scope:
    return Scope(name="epfl", communities=tuple(config.scope.epfl_communities))


def switzerland_scope(config: ZenodoIndexConfig) -> Scope:
    seen: set[str] = set()
    merged: list[str] = []
    for slug in [*config.scope.epfl_communities, *config.scope.switzerland_communities]:
        if slug and slug not in seen:
            seen.add(slug)
            merged.append(slug)
    return Scope(name="switzerland", communities=tuple(merged))


def resolve_scope(name: str, config: ZenodoIndexConfig) -> Scope:
    if name == "epfl":
        return epfl_scope(config)
    if name == "switzerland":
        return switzerland_scope(config)
    message = f"Unknown scope: {name}. Known: epfl, switzerland"
    raise ValueError(message)
