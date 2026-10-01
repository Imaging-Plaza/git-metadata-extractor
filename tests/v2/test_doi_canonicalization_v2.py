"""Behaviour of the v2-canonicalization DOI re-export.

The real implementation lives in the ``open_pulse_sources`` library at
``open_pulse_sources.index._shared.doi``. These tests pin the DOI
normalisation v2 code gets through
`from git_metadata_extractor.canonicalization import doi_iri, parse_doi`.
"""

from __future__ import annotations

from git_metadata_extractor.canonicalization import doi_iri, parse_doi


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
