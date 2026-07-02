"""Smoke for the v2-canonicalization DOI re-export.

The real implementation lives in `src/index/_shared/doi.py` (used by
every catalog backend and the citation_cff parser). This test just
verifies the v2 import alias resolves to the same callable, so v2
code can `from src.v2.canonicalization import doi_iri, parse_doi`
without reaching across the package boundary.
"""

from __future__ import annotations

from open_pulse_sources.index._shared import doi as _shared_doi
from src.v2.canonicalization import doi_iri, parse_doi


def test_v2_re_exports_match_shared_implementation():
    assert doi_iri is _shared_doi.doi_iri
    assert parse_doi is _shared_doi.parse_doi


def test_v2_alias_round_trips_on_canonical_input():
    assert doi_iri("10.5281/zenodo.123") == "https://doi.org/10.5281/zenodo.123"
    assert parse_doi("https://doi.org/10.5281/zenodo.123") == "10.5281/zenodo.123"


def test_v2_alias_handles_legacy_dx_doi_host():
    assert doi_iri("https://dx.doi.org/10.1234/abc") == "https://doi.org/10.1234/abc"


def test_v2_alias_handles_doi_scheme_prefix():
    assert doi_iri("doi:10.1234/abc") == "https://doi.org/10.1234/abc"


def test_v2_alias_returns_none_on_garbage():
    assert doi_iri(None) is None
    assert doi_iri("") is None
