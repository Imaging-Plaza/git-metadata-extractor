"""Scope post-filter for fetched ORCID records.

ORCID's public API does not expose ROR-based filtering, so we accept the
record returned by the provider and inspect its `employment` / `education`
affiliations after the fact:

- **epfl**: an employment or education organization name matches one of
  the configured affiliation aliases (case-insensitive substring).
- **switzerland**: trust OpenAlex-bootstrapped seeds (already country-
  scoped) and otherwise fall back to alias matching — the YAML aliases
  can be expanded for Phase 2.

Returns `(in_scope, reason)` so the persistence layer can record *why*
a record was kept and downstream queries can audit decisions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from src.index.orcid.config import OrcidIndexConfig
    from src.v2.ingest.providers.base import ORCIDAffiliation, ORCIDRecord

ScopeName = Literal["epfl", "switzerland"]


def post_filter_record(
    record: ORCIDRecord,
    *,
    scope: ScopeName,
    config: OrcidIndexConfig,
    discovered_via: str,
) -> tuple[bool, str | None]:
    if scope == "epfl":
        return _alias_match(record, aliases=config.scope.affiliation_aliases)
    if scope == "switzerland":
        if discovered_via in {"openalex", "both"}:
            return True, f"openalex_country={config.scope.country}"
        # Fallback for ORCID-search-only seeds: tolerate alias match.
        return _alias_match(record, aliases=config.scope.affiliation_aliases)
    message = f"Unknown scope: {scope}"
    raise ValueError(message)


def _alias_match(
    record: ORCIDRecord,
    *,
    aliases: list[str],
) -> tuple[bool, str | None]:
    norms = [a.lower().strip() for a in aliases if a and a.strip()]
    if not norms:
        return False, None
    affiliations_by_kind: dict[str, list["ORCIDAffiliation"]] = {
        "employment": list(record.get("employment") or []),
        "education": list(record.get("education") or []),
    }
    for kind, items in affiliations_by_kind.items():
        for affiliation in items:
            org = (affiliation.get("organization") or "").lower().strip()
            if not org:
                continue
            for alias in norms:
                if alias in org:
                    return True, f"{kind}.organization~={alias!r}"
    return False, None
