from __future__ import annotations

from src.index.ror.filter import (
    EUROPE_COUNTRY_CODES,
    filter_countries,
    filter_country_code,
    filter_subtree,
)


def test_subtree_expands_from_epfl_seed_to_child(mini_dump):
    kept = filter_subtree(
        mini_dump,
        seeds=["https://ror.org/02s376052"],
        expand_types=("parent", "child", "related"),
        max_depth=2,
    )
    ids = {r["id"] for r in kept}
    assert "https://ror.org/02s376052" in ids
    assert "https://ror.org/000epfl-cim" in ids
    assert "https://ror.org/05a28rw58" not in ids  # ETHZ not reachable from EPFL


def test_subtree_with_both_seeds(mini_dump):
    kept = filter_subtree(
        mini_dump,
        seeds=["https://ror.org/02s376052", "https://ror.org/05a28rw58"],
        max_depth=2,
    )
    ids = {r["id"] for r in kept}
    assert ids == {
        "https://ror.org/02s376052",
        "https://ror.org/000epfl-cim",
        "https://ror.org/05a28rw58",
    }


def test_subtree_handles_bare_id_seed(mini_dump):
    kept = filter_subtree(mini_dump, seeds=["02s376052"], max_depth=1)
    ids = {r["id"] for r in kept}
    assert "https://ror.org/02s376052" in ids
    assert "https://ror.org/000epfl-cim" in ids


def test_subtree_respects_max_depth(mini_dump):
    kept = filter_subtree(
        mini_dump,
        seeds=["https://ror.org/02s376052"],
        max_depth=0,
    )
    assert {r["id"] for r in kept} == {"https://ror.org/02s376052"}


def test_country_filter_keeps_all_ch_records(mini_dump):
    kept = filter_country_code(mini_dump, "CH")
    ids = {r["id"] for r in kept}
    assert ids == {
        "https://ror.org/02s376052",
        "https://ror.org/000epfl-cim",
        "https://ror.org/05a28rw58",
        "https://ror.org/02k7v4d05",
        "https://ror.org/0042zzz00",
    }
    assert "https://ror.org/042nb2s44" not in ids  # MIT excluded


def test_country_filter_is_case_insensitive(mini_dump):
    assert len(filter_country_code(mini_dump, "ch")) == len(filter_country_code(mini_dump, "CH"))


def test_country_filter_us(mini_dump):
    kept = filter_country_code(mini_dump, "US")
    assert {r["id"] for r in kept} == {"https://ror.org/042nb2s44"}


def test_filter_countries_accepts_set(mini_dump):
    kept = filter_countries(mini_dump, {"CH", "US"})
    ids = {r["id"] for r in kept}
    assert "https://ror.org/02s376052" in ids
    assert "https://ror.org/042nb2s44" in ids


def test_filter_europe_includes_ch_excludes_us(mini_dump):
    kept = filter_countries(mini_dump, EUROPE_COUNTRY_CODES)
    ids = {r["id"] for r in kept}
    assert "https://ror.org/02s376052" in ids       # EPFL (CH)
    assert "https://ror.org/05a28rw58" in ids       # ETHZ (CH)
    assert "https://ror.org/042nb2s44" not in ids   # MIT (US)


def test_europe_country_set_contains_expected_members():
    # Sanity: a handful of well-known European ISO codes must be present.
    for code in ["CH", "DE", "FR", "IT", "GB", "ES", "NL", "PL", "SE"]:
        assert code in EUROPE_COUNTRY_CODES
    # And a non-European code must be absent.
    for code in ["US", "JP", "BR", "ZA", "AU", "CN"]:
        assert code not in EUROPE_COUNTRY_CODES
