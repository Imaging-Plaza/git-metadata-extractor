"""Build the embedding-ready text for one SNSF grant row.

Composes title (preferring English) + keywords + main discipline + the
technical Abstract. Skips the lay summaries — those use simpler language
and would dilute the technical signal we're embedding for.
"""

from __future__ import annotations

from typing import Any, Dict


def _coalesce(*values: Any) -> str:
    for v in values:
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def to_document(grant: Dict[str, Any]) -> str:
    """Flatten a `grants` row dict into a single embedding-ready string.

    `grant` is the dict shape returned by `SnsfStore.fetch_grant`; columns
    are snake_case (per `storage/schema.sql`).
    """
    title = _coalesce(grant.get("title_english"), grant.get("title"))
    keywords = _coalesce(grant.get("keywords"))
    discipline = _coalesce(grant.get("main_discipline"))
    field = _coalesce(grant.get("main_field_of_research"))
    abstract = _coalesce(grant.get("abstract"))

    lines = []
    if title:
        lines.append(f"Title: {title}")
    if discipline:
        lines.append(f"Discipline: {discipline}")
    if field and field != discipline:
        lines.append(f"Field: {field}")
    if keywords:
        lines.append(f"Keywords: {keywords}")
    if abstract:
        lines.append(f"Abstract: {abstract}")
    return "\n".join(lines)


def short_label(grant: Dict[str, Any]) -> str:
    """Used for log lines + reranker fallback when abstract is missing."""
    title = _coalesce(grant.get("title_english"), grant.get("title"))
    inst = _coalesce(grant.get("research_institution"))
    return f"{title} ({inst})" if inst else title
