"""Pure DOI normalization owned by the extraction contract.

Kept compatible with the sources service without importing its index library.
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



__all__ = ["doi_iri", "parse_doi"]
