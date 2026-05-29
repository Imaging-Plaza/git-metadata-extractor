"""Scope filter builder for Zenodo communities.

Each scope resolves to a list of Zenodo community slugs the ingest
loop will iterate. The resolver pulls from two sources, in order:

  1. The ``communities`` DuckDB index (`data/index/communities/duckdb/communities.duckdb`)
     filtered by ``parent_org``. This is the authoritative cross-org
     registry — it's populated from a curated YAML + Zenodo
     ``/api/communities`` auto-discovery, so new lab communities show
     up without anyone editing this file.

  2. The legacy ``ZenodoIndexConfig.scope.<name>_communities`` lists.
     Falls back to these when the communities DuckDB doesn't exist or
     is empty for a parent — preserves the original two-phase
     behaviour (``epfl`` / ``switzerland``) for repos that haven't
     built the communities index.

The two sources are merged and deduped by slug. Records are deduped
by ``zenodo_id`` at upsert time downstream.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from src.index.zenodo_records.config import ZenodoIndexConfig

logger = logging.getLogger(__name__)


ScopeName = Literal[
    "epfl",
    "switzerland",
    "ethz",
    "cern",
    "cern_openlab",
    "all",
]

# Maps the public scope name to the `parent_org` value used in the
# communities index. Anything not in here falls back to the legacy
# `*_communities` config lists only.
_PARENT_ORG_FOR_SCOPE: dict[str, str | None] = {
    "epfl":         "epfl",
    "ethz":         "ethz",
    "cern":         "cern",
    "cern_openlab": "cern_openlab",
    "switzerland":  None,   # composite: epfl + ethz + legacy switzerland list
    "all":          None,   # composite: every parent in the communities index
}


@dataclass(slots=True, frozen=True)
class Scope:
    name: str
    communities: tuple[str, ...]


def _slugs_from_communities_index(parent_org: str | None) -> list[str]:
    """Pull community slugs from the local communities DuckDB.

    Returns an empty list (no error) when the index isn't built yet —
    the legacy hardcoded list takes over downstream.
    """

    try:
        import duckdb  # noqa: PLC0415
        from src.index.zenodo_communities.paths import duckdb_path  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return []
    db_path = duckdb_path()
    if not db_path.exists():
        return []
    try:
        con = duckdb.connect(str(db_path), read_only=True)
    except Exception:  # noqa: BLE001
        logger.exception("communities index open failed")
        return []
    try:
        if parent_org:
            rows = con.execute(
                "SELECT source_slug FROM communities WHERE parent_org = ? AND source = 'zenodo'",
                [parent_org],
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT source_slug FROM communities WHERE source = 'zenodo'",
            ).fetchall()
        return [r[0] for r in rows if r and isinstance(r[0], str)]
    except Exception:  # noqa: BLE001
        logger.exception("communities index query failed (parent=%r)", parent_org)
        return []
    finally:
        try:
            con.close()
        except Exception:  # noqa: BLE001
            pass


def _dedupe(slugs: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for s in slugs:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return tuple(out)


def epfl_scope(config: ZenodoIndexConfig) -> Scope:
    from_index = _slugs_from_communities_index("epfl")
    legacy = list(config.scope.epfl_communities or [])
    return Scope(name="epfl", communities=_dedupe(from_index + legacy))


def ethz_scope(config: ZenodoIndexConfig) -> Scope:
    from_index = _slugs_from_communities_index("ethz")
    # No legacy `ethz_communities` field exists; pure index-driven.
    return Scope(name="ethz", communities=_dedupe(from_index))


def cern_scope(config: ZenodoIndexConfig) -> Scope:
    from_index = _slugs_from_communities_index("cern")
    return Scope(name="cern", communities=_dedupe(from_index))


def cern_openlab_scope(config: ZenodoIndexConfig) -> Scope:
    from_index = _slugs_from_communities_index("cern_openlab")
    return Scope(name="cern_openlab", communities=_dedupe(from_index))


def switzerland_scope(config: ZenodoIndexConfig) -> Scope:
    # EPFL + ETHZ communities from the index, plus the legacy curated
    # `switzerland_communities` list (for slugs we know about that
    # haven't been registered in the communities index yet).
    merged: list[str] = []
    merged.extend(_slugs_from_communities_index("epfl"))
    merged.extend(_slugs_from_communities_index("ethz"))
    merged.extend(config.scope.epfl_communities or [])
    merged.extend(config.scope.switzerland_communities or [])
    return Scope(name="switzerland", communities=_dedupe(merged))


def all_scope(config: ZenodoIndexConfig) -> Scope:
    """Every Zenodo community registered in the communities index."""
    merged: list[str] = list(_slugs_from_communities_index(None))
    merged.extend(config.scope.epfl_communities or [])
    merged.extend(config.scope.switzerland_communities or [])
    return Scope(name="all", communities=_dedupe(merged))


_RESOLVERS = {
    "epfl":         epfl_scope,
    "switzerland":  switzerland_scope,
    "ethz":         ethz_scope,
    "cern":         cern_scope,
    "cern_openlab": cern_openlab_scope,
    "all":          all_scope,
}


def resolve_scope(name: str, config: ZenodoIndexConfig) -> Scope:
    resolver = _RESOLVERS.get(name)
    if resolver is None:
        known = ", ".join(sorted(_RESOLVERS))
        message = f"Unknown scope: {name}. Known: {known}"
        raise ValueError(message)
    return resolver(config)
