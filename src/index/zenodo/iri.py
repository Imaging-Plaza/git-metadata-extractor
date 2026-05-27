"""Canonical IRI helpers for the Zenodo index.

Bare numeric ids (`18314844`) and slugs (`epfl`) used to be the primary
keys; this module promotes them to the dereferenceable URL form that
every other catalog already uses (GitHub, ORCID, ROR, OpenAlex, …).

  record   `18314844`   →   `https://zenodo.org/records/18314844`
  community `epfl`      →   `https://zenodo.org/communities/epfl`

No trailing slash. The schema migration in
`src/index/zenodo/storage/schema.sql` rewrites every PK + FK in
lockstep on next bootstrap.
"""

from __future__ import annotations

_RECORD_PREFIX = "https://zenodo.org/records/"
_COMMUNITY_PREFIX = "https://zenodo.org/communities/"


def record_iri(zenodo_id: str) -> str:
    """`18314844` → `https://zenodo.org/records/18314844`.

    Already-IRI input passes through unchanged so callers can be relaxed
    about double-conversion.
    """
    s = str(zenodo_id).strip()
    if not s:
        return s
    if s.startswith(_RECORD_PREFIX):
        return s.rstrip("/")
    return f"{_RECORD_PREFIX}{s}"


def community_iri(slug: str) -> str:
    """`epfl` → `https://zenodo.org/communities/epfl`. Idempotent."""
    s = str(slug).strip()
    if not s:
        return s
    if s.startswith(_COMMUNITY_PREFIX):
        return s.rstrip("/")
    return f"{_COMMUNITY_PREFIX}{s}"


def parse_record_id(iri_or_bare: str) -> str | None:
    """Inverse of `record_iri`. Returns the bare id, or None on garbage."""
    s = str(iri_or_bare or "").strip()
    if not s:
        return None
    if s.startswith(_RECORD_PREFIX):
        return s[len(_RECORD_PREFIX) :].rstrip("/") or None
    return s  # already bare


def parse_community_slug(iri_or_bare: str) -> str | None:
    """Inverse of `community_iri`. Returns the bare slug, or None on garbage."""
    s = str(iri_or_bare or "").strip()
    if not s:
        return None
    if s.startswith(_COMMUNITY_PREFIX):
        return s[len(_COMMUNITY_PREFIX) :].rstrip("/") or None
    return s


__all__ = [
    "community_iri",
    "parse_community_slug",
    "parse_record_id",
    "record_iri",
]
