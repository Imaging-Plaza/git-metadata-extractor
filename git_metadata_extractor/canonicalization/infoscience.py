"""Shared Infoscience canonical-URL helpers.

Infoscience (EPFL's institutional repository) identifies persons,
organisations (org-units), and publications via UUID4 in its REST API.
The canonical web-facing URL for each entity is:

  - person:     ``https://infoscience.epfl.ch/entities/person/<uuid>``
  - org-unit:   ``https://infoscience.epfl.ch/entities/orgunit/<uuid>``
  - publication:``https://infoscience.epfl.ch/entities/publication/<uuid>``

(Note ``orgunit`` not ``organization`` in the org URL — that's
Infoscience's own URL convention, not ours to change.)

This module mirrors the DOI / ORCID helpers: every helper accepts any
reasonable input shape (bare UUID4 or already-canonical URL), returns
the canonical URL form, and is idempotent on canonical input. Bogus
shapes return ``None``.
"""

from __future__ import annotations

import re

_BASE = "https://infoscience.epfl.ch/entities/"

# Strict UUID4 (matches the regex used in `pulse:*Identifier` schemas).
# Per RFC 4122 UUIDs are case-insensitive; we lowercase before matching.
_UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
)


def _build(kind: str, value: str | None) -> str | None:
    """Build an Infoscience entity URL for ``kind`` (``person``,
    ``orgunit``, ``publication``) given a bare UUID4 OR an
    already-canonical URL. Returns ``None`` when ``value`` is
    malformed or the kind in an input URL doesn't match the
    requested ``kind`` (i.e., calling ``infoscience_person_iri``
    with an org URL fails — surfaces type mismatches loudly)."""
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    # Tolerate trailing slash on canonical input.
    if s.endswith("/"):
        s = s.rstrip("/")
    if s.lower().startswith(_BASE):
        # Already-URL form. Verify kind + UUID shape; reject mismatches.
        path = s[len(_BASE):]
        parts = path.split("/", 1)
        if len(parts) != 2:
            return None
        url_kind, uuid = parts
        url_kind = url_kind.lower()
        uuid = uuid.lower()
        if url_kind != kind:
            return None
        if not _UUID4_RE.fullmatch(uuid):
            return None
        return f"{_BASE}{kind}/{uuid}"
    # Bare UUID4 — accept mixed case per RFC 4122.
    s_lower = s.lower()
    if not _UUID4_RE.fullmatch(s_lower):
        return None
    return f"{_BASE}{kind}/{s_lower}"


def infoscience_person_iri(value: str | None) -> str | None:
    """Canonical Infoscience person URL."""
    return _build("person", value)


def infoscience_org_iri(value: str | None) -> str | None:
    """Canonical Infoscience org-unit URL."""
    return _build("orgunit", value)


def infoscience_article_iri(value: str | None) -> str | None:
    """Canonical Infoscience publication URL."""
    return _build("publication", value)


def infoscience_iri_sql(expr: str, kind: str) -> str:
    """Return a DuckDB scalar SQL expression that URL-ifies a bare UUID4
    column ``expr`` to the canonical Infoscience ``kind`` URL.

    ``kind`` is the URL path segment (``person`` / ``orgunit`` /
    ``publication``). Mirrors :func:`_build` semantics exactly: NULL and
    already-canonical values pass through unchanged, non-UUID4 strings are
    left as-is, and a bare UUID4 is lowercased and prefixed. Used by the
    bulk-SQL ingest path and the bootstrap migration so both agree with the
    per-row Python helpers.
    """
    if kind not in ("person", "orgunit", "publication"):
        msg = f"Unknown infoscience kind: {kind!r}"
        raise ValueError(msg)
    # UUID4 pattern matching `_UUID4_RE` (lowercased input).
    uuid4 = r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
    return (
        f"CASE "
        f"WHEN {expr} IS NULL THEN NULL "
        f"WHEN starts_with(lower({expr}), '{_BASE}') THEN {expr} "
        f"WHEN regexp_full_match(lower({expr}), '{uuid4}') "
        f"THEN '{_BASE}{kind}/' || lower({expr}) "
        f"ELSE {expr} END"
    )


def parse_infoscience_iri(iri: str | None) -> tuple[str, str] | None:
    """Inverse — return ``(kind, uuid)`` for a canonical Infoscience
    URL, or ``None`` on anything else. ``kind`` is one of
    ``person`` / ``orgunit`` / ``publication``.
    """
    if not isinstance(iri, str):
        return None
    s = iri.strip().rstrip("/")
    if not s.lower().startswith(_BASE):
        return None
    rest = s[len(_BASE):]
    parts = rest.split("/", 1)
    if len(parts) != 2:
        return None
    kind, uuid = parts
    kind = kind.lower()
    uuid = uuid.lower()
    if kind not in ("person", "orgunit", "publication"):
        return None
    if not _UUID4_RE.fullmatch(uuid):
        return None
    return kind, uuid


__all__ = [
    "infoscience_article_iri",
    "infoscience_iri_sql",
    "infoscience_org_iri",
    "infoscience_person_iri",
    "parse_infoscience_iri",
]
