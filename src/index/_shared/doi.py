"""Shared DOI canonical-URL helper used across every index module.

Each catalog has its own per-source IRI module
(`src/index/zenodo/iri.py`, `src/index/huggingface/iri.py`, …), but
DOIs aren't source-specific — every catalog that stores publication
metadata should converge on the same `https://doi.org/<…>` form. This
keeps the helper in one place so the rule is uniform.
"""

from __future__ import annotations

_DOI_PREFIX = "https://doi.org/"
_LEGACY_DX = "https://dx.doi.org/"


def doi_iri(doi: str | None) -> str | None:
    """Promote a bare or legacy DOI to canonical `https://doi.org/<doi>`.

    Tolerates every reasonable input shape we've seen on the wire:
    bare (`10.5281/zenodo.123`), `doi:`-prefixed, legacy `dx.doi.org`
    host, trailing slash. Idempotent on canonical input. Returns None
    on empty / whitespace.
    """
    if doi is None:
        return None
    s = str(doi).strip()
    if not s:
        return None
    if s.startswith(_LEGACY_DX):
        s = _DOI_PREFIX + s[len(_LEGACY_DX) :]
    if s.startswith(_DOI_PREFIX):
        return s.rstrip("/")
    if s.lower().startswith("doi:"):
        s = s[len("doi:") :]
    return _DOI_PREFIX + s


def parse_doi(iri_or_bare: str | None) -> str | None:
    """Inverse of `doi_iri`. Returns the bare `10.…` form, or None."""
    if iri_or_bare is None:
        return None
    s = str(iri_or_bare).strip()
    if not s:
        return None
    if s.startswith(_DOI_PREFIX):
        return s[len(_DOI_PREFIX) :].rstrip("/") or None
    if s.startswith(_LEGACY_DX):
        return s[len(_LEGACY_DX) :].rstrip("/") or None
    if s.lower().startswith("doi:"):
        return s[len("doi:") :].rstrip("/") or None
    return s


def migrate_doi_column_to_url(
    conn: object, *, table: str, column: str,
) -> int:
    """Promote `<table>.<column>` to `https://doi.org/<bare>` for every row
    not already in URL form. Idempotent — returns the number of rows
    rewritten.

    Handles the legacy `https://dx.doi.org/` host too.
    """
    # Promote bare rows first, then normalise `dx.doi.org` to `doi.org`.
    bare_count = conn.execute(  # type: ignore[attr-defined]
        f"SELECT COUNT(*) FROM {table} "
        f"WHERE {column} IS NOT NULL "
        f"  AND {column} NOT LIKE 'https://doi.org/%' "
        f"  AND {column} NOT LIKE 'https://dx.doi.org/%'",
    ).fetchone()[0]
    if bare_count:
        conn.execute(  # type: ignore[attr-defined]
            f"UPDATE {table} "
            f"   SET {column} = 'https://doi.org/' || "
            f"           CASE WHEN LOWER(CAST({column} AS VARCHAR)) LIKE 'doi:%' "
            f"                THEN SUBSTRING(CAST({column} AS VARCHAR), 5) "
            f"                ELSE CAST({column} AS VARCHAR) END "
            f" WHERE {column} IS NOT NULL "
            f"   AND {column} NOT LIKE 'https://doi.org/%' "
            f"   AND {column} NOT LIKE 'https://dx.doi.org/%'",
        )
    dx_count = conn.execute(  # type: ignore[attr-defined]
        f"SELECT COUNT(*) FROM {table} "
        f"WHERE {column} LIKE 'https://dx.doi.org/%'",
    ).fetchone()[0]
    if dx_count:
        conn.execute(  # type: ignore[attr-defined]
            f"UPDATE {table} "
            f"   SET {column} = 'https://doi.org/' || "
            f"           SUBSTRING({column}, LENGTH('https://dx.doi.org/') + 1) "
            f" WHERE {column} LIKE 'https://dx.doi.org/%'",
        )
    return bare_count + dx_count


__all__ = ["doi_iri", "migrate_doi_column_to_url", "parse_doi"]
