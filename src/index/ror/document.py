"""Flatten a ROR v2 record into a single embedding-ready text string.

Composes display name + aliases + acronyms + types + city/region/country +
website domain + a short relationship line. The raw record is preserved
elsewhere (records.jsonl) — this module is text-only.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _names_by_type(record: Dict[str, Any]) -> Dict[str, List[str]]:
    grouped: Dict[str, List[str]] = {}
    for entry in record.get("names") or []:
        if not isinstance(entry, dict):
            continue
        value = entry.get("value")
        if not isinstance(value, str) or not value:
            continue
        types = entry.get("types") or []
        for t in types:
            grouped.setdefault(str(t), []).append(value)
    return grouped


def display_name(record: Dict[str, Any]) -> Optional[str]:
    grouped = _names_by_type(record)
    if grouped.get("ror_display"):
        return grouped["ror_display"][0]
    if grouped.get("label"):
        return grouped["label"][0]
    for entries in grouped.values():
        if entries:
            return entries[0]
    return None


def _location_phrase(record: Dict[str, Any]) -> Optional[str]:
    locations = record.get("locations") or []
    if not isinstance(locations, list) or not locations:
        return None
    first = locations[0]
    if not isinstance(first, dict):
        return None
    details = first.get("geonames_details") or {}
    if not isinstance(details, dict):
        return None
    parts = [
        details.get("name"),
        details.get("country_subdivision_name") or details.get("region"),
        details.get("country_name"),
    ]
    parts = [p for p in parts if isinstance(p, str) and p]
    return ", ".join(parts) if parts else None


def _website(record: Dict[str, Any]) -> Optional[str]:
    for link in record.get("links") or []:
        if isinstance(link, dict) and link.get("type") == "website":
            value = link.get("value")
            if isinstance(value, str) and value:
                return value
    domains = record.get("domains") or []
    if isinstance(domains, list) and domains:
        first = domains[0]
        if isinstance(first, str) and first:
            return first
    return None


def _relationships_phrase(record: Dict[str, Any]) -> Optional[str]:
    parts: List[str] = []
    for rel in record.get("relationships") or []:
        if not isinstance(rel, dict):
            continue
        rtype = rel.get("type")
        rlabel = rel.get("label")
        if isinstance(rtype, str) and isinstance(rlabel, str) and rtype and rlabel:
            parts.append(f"{rtype} of {rlabel}")
    return "; ".join(parts) if parts else None


def to_document(record: Dict[str, Any]) -> str:
    """Build the flattened embedding-ready string for one ROR record."""
    grouped = _names_by_type(record)
    name = display_name(record) or record.get("id") or ""

    aliases = grouped.get("alias") or []
    acronyms = grouped.get("acronym") or []
    labels = [
        n for n in grouped.get("label") or []
        if n != name
    ]
    types = record.get("types") or []
    location = _location_phrase(record)
    website = _website(record)
    relationships = _relationships_phrase(record)

    lines: List[str] = [f"Name: {name}"]
    if labels:
        lines.append("Other names: " + "; ".join(labels))
    if aliases:
        lines.append("Aliases: " + "; ".join(aliases))
    if acronyms:
        lines.append("Acronyms: " + "; ".join(acronyms))
    if types:
        lines.append("Types: " + ", ".join(str(t) for t in types if t))
    if location:
        lines.append(f"Location: {location}")
    if website:
        lines.append(f"Website: {website}")
    if relationships:
        lines.append(f"Relationships: {relationships}")

    return "\n".join(lines)
