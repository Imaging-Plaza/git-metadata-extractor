"""Build text representations for ORCID persons + affiliations and chunk.

We embed three kinds of strings:

- **Person card**: name + biography + affiliation list. Coarse but useful
  for "find me a person who works on X" queries.
- **Employment row**: name + role at organization (+ department) over a
  date range. Enables time-bounded queries like "EPFL postdocs 2020-2024".
- **Education row**: same shape as employment.

We delegate token-window slicing to the openalex chunker; only the
text-construction differs.
"""

from __future__ import annotations

from typing import Any

from src.index.openalex.embed.chunker import Chunk, chunk_text

__all__ = [
    "Chunk",
    "chunk_for_affiliation",
    "chunk_for_person",
    "person_card_text",
]


def person_card_text(row: dict[str, Any], affiliations: list[str]) -> str | None:
    """Build the embed-text for a person row. Returns None if there's nothing useful."""
    parts: list[str] = []
    name = row.get("display_name") or _join_name(
        row.get("given_name"),
        row.get("family_name"),
    )
    if name:
        parts.append(name)
    bio = (row.get("biography") or "").strip()
    if bio:
        parts.append(bio)
    if affiliations:
        parts.append("Affiliations: " + ", ".join(sorted({a for a in affiliations if a})))
    if not parts:
        return None
    return "\n\n".join(parts)


def chunk_for_person(
    row: dict[str, Any],
    affiliations: list[str],
    *,
    chunk_tokens: int,
    overlap: int,
) -> list[Chunk]:
    text = person_card_text(row, affiliations)
    if not text:
        return []
    return chunk_text(text, chunk_tokens=chunk_tokens, overlap=overlap)


def chunk_for_affiliation(
    person_name: str | None,
    row: dict[str, Any],
    *,
    chunk_tokens: int,
    overlap: int,
) -> list[Chunk]:
    text = _affiliation_text(person_name, row)
    if not text:
        return []
    return chunk_text(text, chunk_tokens=chunk_tokens, overlap=overlap)


def _affiliation_text(person_name: str | None, row: dict[str, Any]) -> str | None:
    org = (row.get("organization") or "").strip()
    if not org:
        return None
    role = (row.get("role") or "Researcher").strip() or "Researcher"
    dept = (row.get("department") or "").strip()
    start = row.get("start_date") or "?"
    end = row.get("end_date") or "present"
    head = person_name.strip() if person_name else "Researcher"
    org_phrase = f"{org}, {dept}" if dept else org
    return f"{head} — {role} at {org_phrase} ({start} → {end})"


def _join_name(given: str | None, family: str | None) -> str | None:
    parts = [p for p in (given, family) if p]
    return " ".join(parts) if parts else None
