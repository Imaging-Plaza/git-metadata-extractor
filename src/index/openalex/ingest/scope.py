"""Scope filter builders.

Two scopes are supported in v1: EPFL (by ROR) and Switzerland (by country
code). For each scope we provide an entity-aware filter dict because the
filter path differs between Works, Authors, and Institutions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from src.index.openalex.config import OpenAlexIndexConfig

ScopeName = Literal["epfl", "switzerland"]


@dataclass(slots=True, frozen=True)
class ScopeFilters:
    """Filter dicts for a single scope, keyed by OpenAlex entity type."""

    works: dict[str, Any]
    authors: dict[str, Any]
    institutions: dict[str, Any]
    sources: dict[str, Any]


def epfl_scope(config: OpenAlexIndexConfig) -> ScopeFilters:
    ror = config.scope.ror
    return ScopeFilters(
        works={"authorships": {"institutions": {"ror": ror}}},
        authors={"affiliations": {"institution": {"ror": ror}}},
        institutions={"ror": ror},
        # OpenAlex Sources don't accept ROR-based filtering; `host_organization`
        # takes an institution OpenAlex ID. We pull sources unfiltered and
        # later derive the EPFL-relevant subset via joins to ingested works.
        sources={},
    )


def switzerland_scope(config: OpenAlexIndexConfig) -> ScopeFilters:
    country = config.scope.country
    return ScopeFilters(
        works={"authorships": {"institutions": {"country_code": country}}},
        authors={"last_known_institutions": {"country_code": country}},
        institutions={"country_code": country},
        # No `country_code` filter on Sources; we pull unfiltered (see above).
        sources={},
    )


def resolve_scope(name: ScopeName, config: OpenAlexIndexConfig) -> ScopeFilters:
    if name == "epfl":
        return epfl_scope(config)
    if name == "switzerland":
        return switzerland_scope(config)
    message = f"Unknown scope: {name}"
    raise ValueError(message)
