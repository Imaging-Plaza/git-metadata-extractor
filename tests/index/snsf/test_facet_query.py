"""Phase B: facet_query.py — query_grants + facet_counts.

Tests cover:
- Single facet filter (funding_instrument, state, etc.) returns only matching grants.
- text= does ILIKE match on title / abstract / keywords.
- sort= (start_date_desc, start_date_asc, amount_desc, amount_asc) orders correctly.
- limit/offset paginate; total reflects the unfiltered-by-page count.
- country filter selects grants via grant_countries EXISTS join.
- person_number (+optional role) selects grants via grant_persons EXISTS join.
- has_output=["publications"] selects only grants with n_publications > 0.
- facet_counts returns per-facet value→count with another filter active (excluded-self
  semantics: funding_instrument facet not narrowed by an active funding_instrument filter).
- Result rows carry n_publications, n_datasets, n_collaborations output counts.
"""

from __future__ import annotations

import json

import pytest

from src.index.snsf.facet_query import GrantFilters, facet_counts, query_grants
from src.index.snsf.facets import build_facets
from src.index.snsf.storage.duckdb_store import SnsfStore

_BASE = "https://data.snf.ch/grants/grant/"
_G1 = f"{_BASE}200001"
_G2 = f"{_BASE}200002"
_G3 = f"{_BASE}200003"
_G4 = f"{_BASE}200004"

# Expected counts — named constants to satisfy PLR2004
_TOTAL_GRANTS = 4
_TWO = 2
_ONE = 1
_ZERO = 0
_PERSON_99 = 99
_PERSON_77 = 77
_YEAR_2020 = 2020
_YEAR_2018 = 2018
_YEAR_2015 = 2015
_G1_PUBS = 2  # 2 publications for G1
_G1_COLLABS = 1  # 1 collaboration (Germany) for G1
_G4_COLLABS = 1  # 1 collaboration (France) for G4


@pytest.fixture
def store(tmp_path):  # type: ignore[no-untyped-def]
    """Tiny SnsfStore with 4 grants, varied attributes, outputs, country, person."""
    s = SnsfStore.open(tmp_path / "snsf_fq.duckdb")
    conn = s.connect()

    # G1: ProjectFunding / EPFL / active / Biology / Life Sciences / 2020 / CHF 500k
    conn.execute(
        "INSERT INTO grants "
        "(grant_number, title, title_english, abstract, keywords, "
        " funding_instrument, research_institution, state, main_discipline, "
        " main_field_of_research, call_decision_year, start_date, end_date, amount_granted) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            _G1, "Protéines de la membrane cellulaire",
            "Cell membrane proteins",
            "This abstract discusses membrane biology and protein folding.",
            "membrane; protein; biology",
            "ProjectFunding", "EPF Lausanne - EPFL", "Active",
            "Biology", "Life Sciences", 2020,
            "2020-01-01", "2022-12-31", 500_000,
        ],
    )

    # G2: Ambizione / ETH Zurich / Completed / Chemistry / Natural Sciences / 2018 / CHF 300k
    conn.execute(
        "INSERT INTO grants "
        "(grant_number, title, title_english, abstract, keywords, "
        " funding_instrument, research_institution, state, main_discipline, "
        " main_field_of_research, call_decision_year, start_date, end_date, amount_granted) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            _G2, "Synthèse organique avancée",
            "Advanced organic synthesis",
            "Research on catalyst design for sustainable chemistry.",
            "catalyst; chemistry; green",
            "Ambizione", "ETH Zurich", "Completed",
            "Chemistry", "Natural Sciences", 2018,
            "2018-06-01", "2021-05-31", 300_000,
        ],
    )

    # G3: ProjectFunding / UniBern / Active / Physics / Natural Sciences / 2020 / CHF 800k
    conn.execute(
        "INSERT INTO grants "
        "(grant_number, title, title_english, abstract, keywords, "
        " funding_instrument, research_institution, state, main_discipline, "
        " main_field_of_research, call_decision_year, start_date, end_date, amount_granted) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            _G3, "Physique des particules",
            "Particle physics experiment",
            "Collider-based experimental study of high energy phenomena.",
            "physics; particles; collider",
            "ProjectFunding", "University of Bern", "Active",
            "Physics", "Natural Sciences", 2020,
            "2020-03-15", "2023-03-14", 800_000,
        ],
    )

    # G4: NCCR / UniGeneva / Completed / Mathematics / Formal Sciences / 2015 / CHF 1.2M
    conn.execute(
        "INSERT INTO grants "
        "(grant_number, title, title_english, abstract, keywords, "
        " funding_instrument, research_institution, state, main_discipline, "
        " main_field_of_research, call_decision_year, start_date, end_date, amount_granted) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            _G4, "Algèbre et topologie",
            "Algebra and topology",
            "Pure mathematics — algebraic structures and their topological invariants.",
            "algebra; topology; mathematics",
            "NCCR", "University of Geneva", "Completed",
            "Mathematics", "Formal Sciences", 2015,
            "2015-01-01", "2020-12-31", 1_200_000,
        ],
    )

    # Publications: 2 for G1, 1 for G2, 0 for G3, G4
    conn.execute(
        "INSERT INTO output_publications (publication_id, grant_number) VALUES ('pub1', ?)",
        [_G1],
    )
    conn.execute(
        "INSERT INTO output_publications (publication_id, grant_number) VALUES ('pub2', ?)",
        [_G1],
    )
    conn.execute(
        "INSERT INTO output_publications (publication_id, grant_number) VALUES ('pub3', ?)",
        [_G2],
    )

    # Dataset for G3
    conn.execute(
        "INSERT INTO output_datasets (dataset_id, grant_number) VALUES ('ds1', ?)",
        [_G3],
    )

    # Collaboration with country for G1 (Germany) and G4 (France)
    conn.execute(
        "INSERT INTO output_collaborations "
        "(collaboration_id, grant_number, country) VALUES ('col1', ?, 'Germany')",
        [_G1],
    )
    conn.execute(
        "INSERT INTO output_collaborations "
        "(collaboration_id, grant_number, country) VALUES ('col2', ?, 'France')",
        [_G4],
    )

    # Person 99 is responsible_applicant on G1 and G2
    conn.execute(
        "INSERT INTO persons (person_number, responsible_applicant_grants) "
        "VALUES (?, ?)",
        [99, json.dumps([_G1, _G2])],
    )
    # Person 77 is employee on G3
    conn.execute(
        "INSERT INTO persons (person_number, employee_grants) VALUES (?, ?)",
        [77, json.dumps([_G3])],
    )

    # Build the facet tables
    build_facets(s)

    yield s
    s.close()


# ---------------------------------------------------------------------------
# query_grants — basic filter assertions
# ---------------------------------------------------------------------------


def test_no_filter_returns_all(store: SnsfStore) -> None:
    result = query_grants(store, GrantFilters())
    assert result["total"] == _TOTAL_GRANTS
    assert len(result["results"]) == _TOTAL_GRANTS


def test_filter_funding_instrument(store: SnsfStore) -> None:
    result = query_grants(store, GrantFilters(funding_instrument=["ProjectFunding"]))
    assert result["total"] == _TWO
    gnums = {r["grant_number"] for r in result["results"]}
    assert gnums == {_G1, _G3}


def test_filter_state(store: SnsfStore) -> None:
    result = query_grants(store, GrantFilters(state=["Active"]))
    assert result["total"] == _TWO
    gnums = {r["grant_number"] for r in result["results"]}
    assert gnums == {_G1, _G3}


def test_filter_research_institution(store: SnsfStore) -> None:
    result = query_grants(store, GrantFilters(research_institution=["ETH Zurich"]))
    assert result["total"] == _ONE
    assert result["results"][0]["grant_number"] == _G2


def test_filter_main_discipline(store: SnsfStore) -> None:
    result = query_grants(store, GrantFilters(main_discipline=["Physics"]))
    assert result["total"] == _ONE
    assert result["results"][0]["grant_number"] == _G3


def test_filter_main_field_of_research(store: SnsfStore) -> None:
    result = query_grants(store, GrantFilters(main_field_of_research=["Natural Sciences"]))
    assert result["total"] == _TWO
    gnums = {r["grant_number"] for r in result["results"]}
    assert gnums == {_G2, _G3}


def test_filter_call_decision_year(store: SnsfStore) -> None:
    result = query_grants(store, GrantFilters(call_decision_year=[_YEAR_2020]))
    assert result["total"] == _TWO
    gnums = {r["grant_number"] for r in result["results"]}
    assert gnums == {_G1, _G3}


def test_filter_multiple_values_in_list(store: SnsfStore) -> None:
    result = query_grants(store, GrantFilters(state=["Active", "Completed"]))
    assert result["total"] == _TOTAL_GRANTS


def test_filter_country(store: SnsfStore) -> None:
    result = query_grants(store, GrantFilters(country=["Germany"]))
    assert result["total"] == _ONE
    assert result["results"][0]["grant_number"] == _G1


def test_filter_country_multiple(store: SnsfStore) -> None:
    result = query_grants(store, GrantFilters(country=["Germany", "France"]))
    assert result["total"] == _TWO
    gnums = {r["grant_number"] for r in result["results"]}
    assert gnums == {_G1, _G4}


def test_filter_person_number(store: SnsfStore) -> None:
    result = query_grants(store, GrantFilters(person_number=_PERSON_99))
    assert result["total"] == _TWO
    gnums = {r["grant_number"] for r in result["results"]}
    assert gnums == {_G1, _G2}


def test_filter_person_number_with_role(store: SnsfStore) -> None:
    # Person 99 is responsible_applicant for G1 + G2, not employee
    result = query_grants(
        store, GrantFilters(person_number=_PERSON_99, person_role="responsible_applicant"),
    )
    assert result["total"] == _TWO
    gnums = {r["grant_number"] for r in result["results"]}
    assert gnums == {_G1, _G2}

    # With wrong role → no results
    result_none = query_grants(
        store, GrantFilters(person_number=_PERSON_99, person_role="employee"),
    )
    assert result_none["total"] == _ZERO


def test_filter_person_77_employee(store: SnsfStore) -> None:
    result = query_grants(
        store, GrantFilters(person_number=_PERSON_77, person_role="employee"),
    )
    assert result["total"] == _ONE
    assert result["results"][0]["grant_number"] == _G3


def test_filter_has_output_publications(store: SnsfStore) -> None:
    result = query_grants(store, GrantFilters(has_output=["publications"]))
    assert result["total"] == _TWO
    gnums = {r["grant_number"] for r in result["results"]}
    assert gnums == {_G1, _G2}


def test_filter_has_output_datasets(store: SnsfStore) -> None:
    result = query_grants(store, GrantFilters(has_output=["datasets"]))
    assert result["total"] == _ONE
    assert result["results"][0]["grant_number"] == _G3


def test_filter_has_output_multiple(store: SnsfStore) -> None:
    # Must have BOTH publications AND datasets -- no grant has both
    result = query_grants(store, GrantFilters(has_output=["publications", "datasets"]))
    assert result["total"] == _ZERO


def test_filter_start_from(store: SnsfStore) -> None:
    # G1 starts 2020-01-01, G3 2020-03-15, G2 2018, G4 2015
    result = query_grants(store, GrantFilters(start_from="2020-01-01"))
    assert result["total"] == _TWO
    gnums = {r["grant_number"] for r in result["results"]}
    assert gnums == {_G1, _G3}


def test_filter_start_to(store: SnsfStore) -> None:
    result = query_grants(store, GrantFilters(start_to="2018-12-31"))
    assert result["total"] == _TWO
    gnums = {r["grant_number"] for r in result["results"]}
    assert gnums == {_G2, _G4}


def test_filter_end_from(store: SnsfStore) -> None:
    # G3 ends 2023-03-14, G1 ends 2022-12-31, G2 2021-05-31, G4 2020-12-31
    result = query_grants(store, GrantFilters(end_from="2022-01-01"))
    assert result["total"] == _TWO
    gnums = {r["grant_number"] for r in result["results"]}
    assert gnums == {_G1, _G3}


def test_filter_end_to(store: SnsfStore) -> None:
    result = query_grants(store, GrantFilters(end_to="2021-12-31"))
    assert result["total"] == _TWO
    gnums = {r["grant_number"] for r in result["results"]}
    assert gnums == {_G2, _G4}


# ---------------------------------------------------------------------------
# query_grants — text search
# ---------------------------------------------------------------------------


def test_text_matches_title(store: SnsfStore) -> None:
    result = query_grants(store, GrantFilters(), text="membrane")
    assert result["total"] == _ONE
    assert result["results"][0]["grant_number"] == _G1


def test_text_matches_title_english(store: SnsfStore) -> None:
    result = query_grants(store, GrantFilters(), text="organic synthesis")
    assert result["total"] == _ONE
    assert result["results"][0]["grant_number"] == _G2


def test_text_matches_abstract(store: SnsfStore) -> None:
    result = query_grants(store, GrantFilters(), text="collider")
    assert result["total"] == _ONE
    assert result["results"][0]["grant_number"] == _G3


def test_text_matches_keywords(store: SnsfStore) -> None:
    result = query_grants(store, GrantFilters(), text="topology")
    assert result["total"] == _ONE
    assert result["results"][0]["grant_number"] == _G4


def test_text_case_insensitive(store: SnsfStore) -> None:
    result = query_grants(store, GrantFilters(), text="MEMBRANE")
    assert result["total"] == _ONE


def test_text_no_match(store: SnsfStore) -> None:
    result = query_grants(store, GrantFilters(), text="xyzzyx_nomatch")
    assert result["total"] == _ZERO


def test_text_combined_with_filter(store: SnsfStore) -> None:
    # text="protein" matches G1 but filter state=Completed -> 0 results
    result = query_grants(
        store, GrantFilters(state=["Completed"]), text="protein",
    )
    assert result["total"] == _ZERO


# ---------------------------------------------------------------------------
# query_grants — sort
# ---------------------------------------------------------------------------


def test_sort_start_date_desc(store: SnsfStore) -> None:
    result = query_grants(store, GrantFilters(), sort="start_date_desc", limit=_TOTAL_GRANTS)
    dates = [r["start_date"] for r in result["results"]]
    # First two are 2020-xx-xx, last is 2015
    assert dates == sorted(dates, reverse=True)


def test_sort_start_date_asc(store: SnsfStore) -> None:
    result = query_grants(store, GrantFilters(), sort="start_date_asc", limit=_TOTAL_GRANTS)
    dates = [r["start_date"] for r in result["results"] if r["start_date"] is not None]
    assert dates == sorted(dates)


def test_sort_amount_desc(store: SnsfStore) -> None:
    result = query_grants(store, GrantFilters(), sort="amount_desc", limit=_TOTAL_GRANTS)
    amounts = [r["amount_granted"] for r in result["results"]]
    assert amounts == sorted(amounts, reverse=True)


def test_sort_amount_asc(store: SnsfStore) -> None:
    result = query_grants(store, GrantFilters(), sort="amount_asc", limit=_TOTAL_GRANTS)
    amounts = [r["amount_granted"] for r in result["results"]]
    assert amounts == sorted(amounts)


def test_sort_unknown_falls_back_to_start_date_desc(store: SnsfStore) -> None:
    result = query_grants(store, GrantFilters(), sort="bogus_sort", limit=_TOTAL_GRANTS)
    dates = [r["start_date"] for r in result["results"] if r["start_date"] is not None]
    assert dates == sorted(dates, reverse=True)


# ---------------------------------------------------------------------------
# query_grants — pagination
# ---------------------------------------------------------------------------


def test_limit(store: SnsfStore) -> None:
    result = query_grants(store, GrantFilters(), limit=_TWO)
    assert len(result["results"]) == _TWO
    assert result["total"] == _TOTAL_GRANTS  # total is unaffected by limit


def test_offset(store: SnsfStore) -> None:
    all_result = query_grants(
        store, GrantFilters(), sort="start_date_asc", limit=_TOTAL_GRANTS,
    )
    all_gnums = [r["grant_number"] for r in all_result["results"]]

    page1 = query_grants(
        store, GrantFilters(), sort="start_date_asc", limit=_TWO, offset=0,
    )
    page2 = query_grants(
        store, GrantFilters(), sort="start_date_asc", limit=_TWO, offset=_TWO,
    )

    assert [r["grant_number"] for r in page1["results"]] == all_gnums[:_TWO]
    assert [r["grant_number"] for r in page2["results"]] == all_gnums[_TWO:]


def test_total_constant_across_pages(store: SnsfStore) -> None:
    page1 = query_grants(store, GrantFilters(), limit=1, offset=0)
    page2 = query_grants(store, GrantFilters(), limit=1, offset=1)
    assert page1["total"] == page2["total"] == _TOTAL_GRANTS


# ---------------------------------------------------------------------------
# query_grants — result shape
# ---------------------------------------------------------------------------


def test_result_row_has_expected_columns(store: SnsfStore) -> None:
    result = query_grants(
        store, GrantFilters(funding_instrument=["ProjectFunding"]), sort="amount_asc", limit=1,
    )
    row = result["results"][0]
    expected_cols = {
        "grant_number", "title", "title_english", "responsible_applicant",
        "research_institution", "main_discipline", "funding_instrument",
        "keywords", "state", "start_date", "end_date", "amount_granted",
        "n_publications", "n_datasets", "n_collaborations",
    }
    assert expected_cols.issubset(row.keys())


def test_result_output_counts_g1(store: SnsfStore) -> None:
    result = query_grants(store, GrantFilters(funding_instrument=["ProjectFunding"]))
    rows_by_gnum = {r["grant_number"]: r for r in result["results"]}
    g1 = rows_by_gnum[_G1]
    assert g1["n_publications"] == _G1_PUBS
    assert g1["n_collaborations"] == _G1_COLLABS
    assert g1["n_datasets"] == _ZERO


def test_result_output_counts_g4_zeros(store: SnsfStore) -> None:
    # G4 has no publications, no datasets; it does have 1 collaboration (France)
    result = query_grants(store, GrantFilters(funding_instrument=["NCCR"]))
    assert result["total"] == _ONE
    row = result["results"][0]
    assert row["n_publications"] == _ZERO
    assert row["n_datasets"] == _ZERO
    assert row["n_collaborations"] == _G4_COLLABS  # France collaboration inserted in fixture


# ---------------------------------------------------------------------------
# facet_counts — basic shape
# ---------------------------------------------------------------------------


def test_facet_counts_returns_all_facets(store: SnsfStore) -> None:
    fc = facet_counts(store, GrantFilters())
    expected_facets = {
        "funding_instrument", "research_institution", "state",
        "main_discipline", "main_field_of_research", "call_decision_year",
        "country",
    }
    assert expected_facets == set(fc.keys())


def test_facet_counts_no_filter_funding_instrument(store: SnsfStore) -> None:
    fc = facet_counts(store, GrantFilters())
    fi_counts = {item["value"]: item["count"] for item in fc["funding_instrument"]}
    assert fi_counts.get("ProjectFunding") == _TWO
    assert fi_counts.get("Ambizione") == _ONE
    assert fi_counts.get("NCCR") == _ONE


def test_facet_counts_country(store: SnsfStore) -> None:
    fc = facet_counts(store, GrantFilters())
    c_counts = {item["value"]: item["count"] for item in fc["country"]}
    assert c_counts.get("Germany") == _ONE
    assert c_counts.get("France") == _ONE


def test_facet_counts_ordered_by_count_desc(store: SnsfStore) -> None:
    fc = facet_counts(store, GrantFilters())
    # state: Active=2, Completed=2 — at least counts are descending/tied
    state_counts = [item["count"] for item in fc["state"]]
    assert state_counts == sorted(state_counts, reverse=True)


# ---------------------------------------------------------------------------
# facet_counts — excluded-self semantics
# ---------------------------------------------------------------------------


def test_facet_counts_excluded_self_funding_instrument(store: SnsfStore) -> None:
    """With funding_instrument=["ProjectFunding"] active, the funding_instrument
    facet counts are NOT narrowed by that filter — they still show all 3 schemes."""
    fc = facet_counts(store, GrantFilters(funding_instrument=["ProjectFunding"]))
    fi_counts = {item["value"]: item["count"] for item in fc["funding_instrument"]}
    # All 4 grants (all schemes) should appear — self-excluded
    assert "Ambizione" in fi_counts
    assert "NCCR" in fi_counts
    assert "ProjectFunding" in fi_counts


def test_facet_counts_other_facets_narrowed_by_active_filter(store: SnsfStore) -> None:
    """With funding_instrument=["ProjectFunding"] active, other facets (e.g. state)
    ARE narrowed — only the two ProjectFunding grants (G1 Active, G3 Active) appear."""
    fc = facet_counts(store, GrantFilters(funding_instrument=["ProjectFunding"]))
    state_counts = {item["value"]: item["count"] for item in fc["state"]}
    # Both G1 and G3 are Active -> _TWO; Completed should not appear (G2/G4 are Ambizione/NCCR)
    assert state_counts.get("Active") == _TWO
    assert "Completed" not in state_counts


def test_facet_counts_country_excluded_self(store: SnsfStore) -> None:
    """With country=["Germany"] active, country facet counts are NOT narrowed."""
    fc = facet_counts(store, GrantFilters(country=["Germany"]))
    c_counts = {item["value"]: item["count"] for item in fc["country"]}
    assert "France" in c_counts


def test_facet_counts_call_year_excluded_self(store: SnsfStore) -> None:
    """With call_decision_year=[2020] active, call_decision_year facet not narrowed."""
    fc = facet_counts(store, GrantFilters(call_decision_year=[_YEAR_2020]))
    yr_values = {item["value"] for item in fc["call_decision_year"]}
    assert _YEAR_2018 in yr_values
    assert _YEAR_2015 in yr_values


def test_facet_counts_with_text_filter(store: SnsfStore) -> None:
    """text= narrows the base result set for all facets."""
    fc = facet_counts(store, GrantFilters(), text="chemistry")
    # Only G2 matches "chemistry" in keywords/abstract
    fi_counts = {item["value"]: item["count"] for item in fc["funding_instrument"]}
    assert fi_counts.get("Ambizione") == _ONE
    assert "ProjectFunding" not in fi_counts
