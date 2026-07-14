"""Shared ETH Zürich Research Collection canonical-URL helpers.

The Research Collection (ETH Zürich's DSpace institutional repository)
identifies persons, organisations (org-units), and publications via UUID4
in its REST API. The canonical web-facing URL for each entity is:

  - person:     ``https://www.research-collection.ethz.ch/entities/person/<uuid>``
  - org-unit:   ``https://www.research-collection.ethz.ch/entities/orgunit/<uuid>``
  - publication:``https://www.research-collection.ethz.ch/entities/publication/<uuid>``

(Note ``orgunit`` not ``organization`` in the org URL — that's DSpace's own
URL convention, not ours to change.)

Mirrors the Infoscience helpers: every helper accepts any reasonable input
shape (bare UUID4 or already-canonical URL), returns the canonical URL form,
and is idempotent on canonical input. Bogus shapes return ``None``.
"""

from __future__ import annotations

import re

_BASE = "https://www.research-collection.ethz.ch/entities/"

# Strict UUID4. Per RFC 4122 UUIDs are case-insensitive; we lowercase first.
_UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
)


def _build(kind: str, value: str | None) -> str | None:
    """Build a Research Collection entity URL for ``kind`` (``person``,
    ``orgunit``, ``publication``) given a bare UUID4 OR an already-canonical
    URL. Returns ``None`` when ``value`` is malformed or the kind in an input
    URL doesn't match the requested ``kind``."""
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    if s.endswith("/"):
        s = s.rstrip("/")
    if s.lower().startswith(_BASE):
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
    s_lower = s.lower()
    if not _UUID4_RE.fullmatch(s_lower):
        return None
    return f"{_BASE}{kind}/{s_lower}"


def ethz_person_iri(value: str | None) -> str | None:
    """Canonical Research Collection person URL."""
    return _build("person", value)


def ethz_org_iri(value: str | None) -> str | None:
    """Canonical Research Collection org-unit URL."""
    return _build("orgunit", value)


def ethz_article_iri(value: str | None) -> str | None:
    """Canonical Research Collection publication URL."""
    return _build("publication", value)


def ethz_iri_sql(expr: str, kind: str) -> str:
    """Return a DuckDB scalar SQL expression that URL-ifies a bare UUID4
    column ``expr`` to the canonical Research Collection ``kind`` URL.

    Mirrors :func:`_build`: NULL and already-canonical values pass through,
    non-UUID4 strings are left as-is, and a bare UUID4 is lowercased and
    prefixed. Shared by the bulk-SQL ingest path and the bootstrap migration.
    """
    if kind not in ("person", "orgunit", "publication"):
        msg = f"Unknown ethz kind: {kind!r}"
        raise ValueError(msg)
    uuid4 = r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
    return (
        f"CASE "
        f"WHEN {expr} IS NULL THEN NULL "
        f"WHEN starts_with(lower({expr}), '{_BASE}') THEN {expr} "
        f"WHEN regexp_full_match(lower({expr}), '{uuid4}') "
        f"THEN '{_BASE}{kind}/' || lower({expr}) "
        f"ELSE {expr} END"
    )


def parse_ethz_iri(iri: str | None) -> tuple[str, str] | None:
    """Inverse — return ``(kind, uuid)`` for a canonical Research Collection
    URL, or ``None`` on anything else."""
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
    "ethz_article_iri",
    "ethz_iri_sql",
    "ethz_org_iri",
    "ethz_person_iri",
    "parse_ethz_iri",
]
