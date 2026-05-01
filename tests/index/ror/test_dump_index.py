from __future__ import annotations

import pytest

from src.index.ror.dump_index import DumpIndex


@pytest.fixture
def index(mini_dump):
    return DumpIndex.from_records(mini_dump)


def test_size_counts_unique_records(index):
    assert index.size > 0
    # Each record is registered under both URL and bare-ID keys; size doesn't count keys.
    assert index.size >= 6 * 2  # at least 12 keys


def test_exact_lookup_by_url(index):
    record = index.by_id("https://ror.org/02s376052")
    assert record is not None
    assert "EPFL" in [n["value"] for n in record["names"] if "acronym" in n.get("types", [])]


def test_exact_lookup_by_bare_id(index):
    assert index.by_id("02s376052") is not None
    assert index.by_id("042nb2s44")["names"][0]["value"] == "Massachusetts Institute of Technology"


def test_search_by_name_token(index):
    results = index.search(text="ETH Zurich", limit=5)
    ids = [r.ror_id for r in results]
    assert "https://ror.org/05a28rw58" in ids


def test_search_with_country_filter(index):
    results = index.search(text="university", country="CH", limit=10)
    ids = {r.ror_id for r in results}
    assert "https://ror.org/02k7v4d05" in ids  # Universität Bern
    assert "https://ror.org/042nb2s44" not in ids  # MIT (US) excluded


def test_search_accent_folding_matches_ecole_without_accents(index):
    results = index.search(text="Ecole polytechnique federale", limit=5)
    ids = [r.ror_id for r in results]
    assert "https://ror.org/02s376052" in ids


def test_search_german_umlaut_folding(index):
    results = index.search(text="Universitat Bern", limit=5)
    ids = [r.ror_id for r in results]
    assert "https://ror.org/02k7v4d05" in ids


def test_search_country_only(index):
    results = index.search(country="US", limit=10)
    assert {r.ror_id for r in results} == {"https://ror.org/042nb2s44"}


def test_search_returns_empty_on_no_match(index):
    assert index.search(text="zzzzzzzz_no_such_org") == []


def test_from_json_path_loads_fixture(mini_dump_path):
    idx = DumpIndex.from_json_path(mini_dump_path)
    assert idx.by_id("02s376052") is not None
