"""Subset filters over a ROR v2 dump.

Two modes:
  - `epfl_ethz`: BFS from seed ROR IDs over `relationships[]` whose type is in
    the allowed set (default: parent, child, related), bounded depth.
  - `switzerland`: keep records where any
    `locations[*].geonames_details.country_code == "CH"`.

Operates on raw v2 records (dicts) — no Pydantic round-trip, no normalization.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Dict, FrozenSet, Iterable, List, Sequence, Set


EUROPE_COUNTRY_CODES: FrozenSet[str] = frozenset({
    "AD", "AL", "AT", "BA", "BE", "BG", "BY", "CH", "CY", "CZ",
    "DE", "DK", "EE", "ES", "FI", "FO", "FR", "GB", "GG", "GI",
    "GR", "HR", "HU", "IE", "IM", "IS", "IT", "JE", "LI", "LT",
    "LU", "LV", "MC", "MD", "ME", "MK", "MT", "NL", "NO", "PL",
    "PT", "RO", "RS", "RU", "SE", "SI", "SJ", "SK", "SM", "TR",
    "UA", "VA", "XK",
})


def _normalize_id(value: str) -> str:
    """Strip trailing slash; keep `https://ror.org/XXXX` form."""
    return value.rstrip("/").strip()


def _bare_id(ror_id: str) -> str:
    return _normalize_id(ror_id).rsplit("/", 1)[-1]


def index_by_id(records: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Return `{ror_id_url: record}` plus alias entries keyed by bare ID."""
    out: Dict[str, Dict[str, Any]] = {}
    for record in records:
        rid = record.get("id")
        if isinstance(rid, str) and rid:
            normalized = _normalize_id(rid)
            out[normalized] = record
            out[_bare_id(normalized)] = record
    return out


def filter_countries(
    records: Iterable[Dict[str, Any]],
    country_codes: Iterable[str],
) -> List[Dict[str, Any]]:
    """Keep records where any location's country_code matches the allowed set."""
    targets = {c.upper() for c in country_codes}
    kept: List[Dict[str, Any]] = []
    for record in records:
        for loc in record.get("locations") or []:
            if not isinstance(loc, dict):
                continue
            details = loc.get("geonames_details") or {}
            if not isinstance(details, dict):
                continue
            cc = details.get("country_code")
            if isinstance(cc, str) and cc.upper() in targets:
                kept.append(record)
                break
    return kept


def filter_country_code(
    records: Iterable[Dict[str, Any]],
    country_code: str,
) -> List[Dict[str, Any]]:
    """Keep records whose location matches a single ISO-3166 alpha-2 code."""
    return filter_countries(records, [country_code])


def filter_subtree(
    records: Iterable[Dict[str, Any]],
    seeds: Sequence[str],
    *,
    expand_types: Sequence[str] = ("parent", "child", "related"),
    max_depth: int = 2,
) -> List[Dict[str, Any]]:
    """BFS from seed ROR IDs over allowed relationship types.

    Records list is materialized once into an id-indexed dict; relationships
    that point to IDs absent from the dump are silently skipped.
    """
    by_id = index_by_id(records)
    allowed = {t.lower() for t in expand_types}

    seen: Set[str] = set()
    queue: deque[tuple[str, int]] = deque()
    for seed in seeds:
        seed_norm = _normalize_id(seed)
        target = by_id.get(seed_norm) or by_id.get(_bare_id(seed_norm))
        if target is None:
            continue
        rid = _normalize_id(str(target.get("id")))
        if rid not in seen:
            seen.add(rid)
            queue.append((rid, 0))

    kept: List[Dict[str, Any]] = []
    while queue:
        rid, depth = queue.popleft()
        record = by_id.get(rid)
        if record is None:
            continue
        kept.append(record)
        if depth >= max_depth:
            continue
        for rel in record.get("relationships") or []:
            if not isinstance(rel, dict):
                continue
            rtype = rel.get("type")
            rid_next = rel.get("id")
            if not isinstance(rtype, str) or not isinstance(rid_next, str):
                continue
            if rtype.lower() not in allowed:
                continue
            rid_next_norm = _normalize_id(rid_next)
            if rid_next_norm in seen:
                continue
            if rid_next_norm not in by_id and _bare_id(rid_next_norm) not in by_id:
                continue
            seen.add(rid_next_norm)
            queue.append((rid_next_norm, depth + 1))

    return kept
