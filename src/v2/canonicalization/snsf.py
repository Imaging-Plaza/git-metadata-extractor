"""Canonical SNSF grant URL builder for index ids.

SNSF's native grant id is a bare integer ``GrantNumber`` (from the P3 bulk
CSV). v3.0.0 stores the canonical grant URL as the id — consistent with the
github / huggingface / dockerhub / orcid / infoscience / ethz index ids:

  grant -> https://data.snf.ch/grants/grant/<grant_number>

Qdrant point ids must be uint64 or UUID (never an arbitrary string), so the
search layer derives a deterministic ``uuid5`` of the grant URL — exactly the
scheme the infoscience / ethz indices use. The URL remains the id everywhere a
consumer sees it (DuckDB PK, Qdrant payload, search-hit id).
"""

from __future__ import annotations

import re
import uuid

_BASE = "https://data.snf.ch/grants/grant/"

# Shared namespace for deriving Qdrant point ids from grant URLs.
_POINT_NAMESPACE = uuid.UUID("b3d1f4a2-7c6e-4d58-9f0a-2e5c8b1d6a47")

_INT_RE = re.compile(r"^\d+$")


def snsf_grant_iri(value: object) -> str | None:
    """Canonical SNSF grant URL for a bare grant number (int or str) or an
    already-canonical URL.

    Returns None on empty/invalid input. Idempotent: a value already under
    ``https://data.snf.ch/grants/grant/`` is returned unchanged (trailing
    slash trimmed); an ``http://`` form is upgraded to ``https://``.
    """
    if value is None:
        return None
    if isinstance(value, int):
        return f"{_BASE}{value}"
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    if s.lower().startswith(_BASE):
        return s.rstrip("/")
    if s.lower().startswith("http://data.snf.ch/grants/grant/"):
        return ("https://" + s.split("://", 1)[1]).rstrip("/")
    if _INT_RE.fullmatch(s):
        return f"{_BASE}{s}"
    return None


def parse_snsf_grant(value: object) -> int | None:
    """Inverse of :func:`snsf_grant_iri`: extract the bare integer grant
    number. Accepts a canonical URL, a bare numeric string, or an int.
    Returns None on anything else."""
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return None
    s = value.strip().rstrip("/")
    if not s:
        return None
    if s.lower().startswith(_BASE) or s.lower().startswith(
        "http://data.snf.ch/grants/grant/",
    ):
        tail = s.rsplit("/", 1)[-1]
        return int(tail) if _INT_RE.fullmatch(tail) else None
    return int(s) if _INT_RE.fullmatch(s) else None


def snsf_grant_point_id(value: object) -> str:
    """Deterministic Qdrant point id (uuid5) for a grant, keyed by its
    canonical URL. Accepts a URL or a bare grant number."""
    url = snsf_grant_iri(value) or str(value)
    return str(uuid.uuid5(_POINT_NAMESPACE, url))


def snsf_grant_iri_sql(expr: str) -> str:
    """Return a DuckDB scalar SQL expression that URL-ifies a bare integer
    grant number column ``expr`` to the canonical grant URL.

    NULL passes through; an already-canonical URL passes through; a bare
    integer (cast to text) is prefixed. Shared by the bulk-CSV load path and
    the bootstrap migration so they agree with :func:`snsf_grant_iri`.
    """
    return (
        f"CASE "
        f"WHEN {expr} IS NULL THEN NULL "
        f"WHEN starts_with(lower(CAST({expr} AS VARCHAR)), '{_BASE}') "
        f"THEN CAST({expr} AS VARCHAR) "
        f"WHEN regexp_full_match(CAST({expr} AS VARCHAR), '\\d+') "
        f"THEN '{_BASE}' || CAST({expr} AS VARCHAR) "
        f"ELSE CAST({expr} AS VARCHAR) END"
    )


__all__ = [
    "parse_snsf_grant",
    "snsf_grant_iri",
    "snsf_grant_iri_sql",
    "snsf_grant_point_id",
]
