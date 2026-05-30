"""Per-scope WHERE-clause derivation against the `grants` table.

The bulk CSVs from data.snf.ch carry the `ResearchInstitution` display name
(not a UUID), so scope filters here use exact string matches against that
column. The mapping is the inverse of the API's UUID-based filter table
documented in `.internal/snsf/README.md`.
"""

from __future__ import annotations

from typing import Literal

ScopeMode = Literal["epfl", "ethz", "eth_domain", "switzerland"]


# `(where_clause, params)` for each scope. Used by
# `SnsfStore.replace_scope_records_by_filter`.
SCOPE_WHERE: dict[ScopeMode, tuple[str, list]] = {
    "epfl": ("research_institution = ?", ["EPF Lausanne – EPFL"]),
    "ethz": ("research_institution = ?", ["ETH Zurich – ETHZ"]),
    "eth_domain": (
        "research_institution_type = ?",
        ["ETH Domain"],
    ),
    "switzerland": ("1=1", []),  # whole dump — every grant in P3 is CH-resident
}


def where_for(scope_mode: str) -> tuple[str, list]:
    if scope_mode not in SCOPE_WHERE:
        msg = (
            f"Unknown scope_mode {scope_mode!r}. "
            f"Valid: {sorted(SCOPE_WHERE.keys())}"
        )
        raise ValueError(msg)
    return SCOPE_WHERE[scope_mode]  # type: ignore[index]


__all__ = ["SCOPE_WHERE", "ScopeMode", "where_for"]
