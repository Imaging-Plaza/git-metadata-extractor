"""Phase D: SNSF LLM agent tool — SnsfGrantsProvider + tool factories.

Tests cover:
- provider.search(GrantFilters(state=["Completed"])) returns matching thin rows.
- provider.fetch(<grant_url>) returns the full row incl. abstract.
- provider.facets(...) returns a dict of facet counts.
- make_search_snsf_grants_tool(provider) returns a Tool with the right name,
  and calling its .function(state=["Completed"]) returns the matching rows.
- Absent-store guard: provider pointed at a non-existent path → search returns [],
  fetch returns None (no exception).
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from open_pulse_sources.index.snsf.facet_query import GrantFilters
from open_pulse_sources.index.snsf.facets import build_facets
from open_pulse_sources.index.snsf.storage.duckdb_store import SnsfStore
from git_metadata_extractor.agents.llm.agent_tools.snsf_grants import (
    make_fetch_snsf_grant_tool,
    make_search_snsf_grants_tool,
    make_snsf_grant_facets_tool,
)
from git_metadata_extractor.providers.snsf_grants import SnsfGrantsProvider

_BASE = "https://data.snf.ch/grants/grant/"
_G1 = f"{_BASE}300001"
_G2 = f"{_BASE}300002"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Build and seed a tiny SnsfStore, close it, then return its path.

    The store is fully closed before the fixture yields so that the
    SnsfGrantsProvider (which opens a *read-only* DuckDB connection) can
    open the same file without a conflicting-configuration error.
    """
    path = tmp_path / "snsf_tool_test.duckdb"
    s = SnsfStore.open(path)
    conn = s.connect()

    # Grant 1 — Active / ProjectFunding / EPFL
    conn.execute(
        "INSERT INTO grants "
        "(grant_number, title, title_english, abstract, keywords, "
        " funding_instrument, research_institution, state, main_discipline, "
        " main_field_of_research, call_decision_year, start_date, end_date, amount_granted) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            _G1, "Projet actif en biologie",
            "Active biology project",
            "Detailed abstract about cell membrane proteins.",
            "membrane; protein; biology",
            "ProjectFunding", "EPF Lausanne - EPFL", "Active",
            "Biology", "Life Sciences", 2022,
            "2022-01-01", "2024-12-31", 450_000,
        ],
    )

    # G2: Completed / Ambizione / ETH Zurich
    conn.execute(
        "INSERT INTO grants "
        "(grant_number, title, title_english, abstract, keywords, "
        " funding_instrument, research_institution, state, main_discipline, "
        " main_field_of_research, call_decision_year, start_date, end_date, amount_granted) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            _G2, "Synthèse chimique verte",
            "Green chemical synthesis",
            "Completed study on sustainable catalysis and green chemistry methods.",
            "catalyst; green; chemistry",
            "Ambizione", "ETH Zurich", "Completed",
            "Chemistry", "Natural Sciences", 2018,
            "2018-06-01", "2021-05-31", 300_000,
        ],
    )

    # One publication output for G1
    conn.execute(
        "INSERT INTO output_publications (publication_id, grant_number) VALUES ('pub1', ?)",
        [_G1],
    )

    # One person linked to both grants
    conn.execute(
        "INSERT INTO persons (person_number, responsible_applicant_grants) VALUES (?, ?)",
        [42, json.dumps([_G1, _G2])],
    )

    build_facets(s)
    # Close the store so the provider can open the file read-only.
    s.close()
    return path


@pytest.fixture
def provider(db_path: Path) -> SnsfGrantsProvider:
    return SnsfGrantsProvider(store_path=db_path)


# ---------------------------------------------------------------------------
# SnsfGrantsProvider.search
# ---------------------------------------------------------------------------


def test_provider_search_returns_matching_thin_rows(provider: SnsfGrantsProvider) -> None:
    rows = provider.search(GrantFilters(state=["Completed"]))
    assert len(rows) == 1
    row = rows[0]
    assert row["grant_number"] == _G2
    assert row["state"] == "Completed"
    # thin row — has title but NOT abstract (query_grants does not select abstract)
    assert "title" in row
    assert "abstract" not in row


def test_provider_search_all_no_filter(provider: SnsfGrantsProvider) -> None:
    rows = provider.search(GrantFilters())
    assert len(rows) == 2  # noqa: PLR2004


def test_provider_search_with_text(provider: SnsfGrantsProvider) -> None:
    rows = provider.search(GrantFilters(), text="catalysis")
    assert len(rows) == 1
    assert rows[0]["grant_number"] == _G2


def test_provider_search_limit(provider: SnsfGrantsProvider) -> None:
    rows = provider.search(GrantFilters(), limit=1)
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# SnsfGrantsProvider.fetch
# ---------------------------------------------------------------------------


def test_provider_fetch_returns_full_row_with_abstract(provider: SnsfGrantsProvider) -> None:
    row = provider.fetch(_G1)
    assert row is not None
    assert row["grant_number"] == _G1
    # full row includes abstract
    assert row["abstract"] == "Detailed abstract about cell membrane proteins."


def test_provider_fetch_returns_none_for_missing_grant(provider: SnsfGrantsProvider) -> None:
    row = provider.fetch("https://data.snf.ch/grants/grant/999999")
    assert row is None


# ---------------------------------------------------------------------------
# SnsfGrantsProvider.facets
# ---------------------------------------------------------------------------


def test_provider_facets_returns_dict(provider: SnsfGrantsProvider) -> None:
    facets = provider.facets(GrantFilters())
    assert isinstance(facets, dict)
    # state facet should contain both Active and Completed
    state_values = {item["value"] for item in facets.get("state", [])}
    assert "Active" in state_values
    assert "Completed" in state_values


def test_provider_facets_with_text_filter(provider: SnsfGrantsProvider) -> None:
    facets = provider.facets(GrantFilters(), text="biology")
    assert isinstance(facets, dict)


# ---------------------------------------------------------------------------
# Absent-store guard
# ---------------------------------------------------------------------------


def test_absent_store_search_returns_empty(tmp_path: Path) -> None:
    p = SnsfGrantsProvider(store_path=tmp_path / "nonexistent.duckdb")
    result = p.search(GrantFilters())
    assert result == []


def test_absent_store_fetch_returns_none(tmp_path: Path) -> None:
    p = SnsfGrantsProvider(store_path=tmp_path / "nonexistent.duckdb")
    result = p.fetch(_G1)
    assert result is None


def test_absent_store_facets_returns_empty_dict(tmp_path: Path) -> None:
    p = SnsfGrantsProvider(store_path=tmp_path / "nonexistent.duckdb")
    result = p.facets(GrantFilters())
    assert result == {}


# ---------------------------------------------------------------------------
# Provider is cheap to construct (does NOT open/create the DB)
# ---------------------------------------------------------------------------


def test_provider_construction_does_not_create_db(tmp_path: Path) -> None:
    db_path = tmp_path / "no_create.duckdb"
    _p = SnsfGrantsProvider(store_path=db_path)
    assert not db_path.exists(), "constructor must not create the DB file"


# ---------------------------------------------------------------------------
# Tool factories
# ---------------------------------------------------------------------------


def _run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_make_search_snsf_grants_tool_name_and_result(provider: SnsfGrantsProvider) -> None:
    tool = make_search_snsf_grants_tool(provider)
    assert tool.name == "search_snsf_grants"

    # Call the underlying function with state filter → only Completed grant
    rows = _run(tool.function(state=["Completed"]))
    assert len(rows) == 1
    assert rows[0]["grant_number"] == _G2


def test_make_search_snsf_grants_tool_no_filter(provider: SnsfGrantsProvider) -> None:
    tool = make_search_snsf_grants_tool(provider)
    rows = _run(tool.function())
    assert len(rows) == 2  # noqa: PLR2004


def test_make_snsf_grant_facets_tool_name(provider: SnsfGrantsProvider) -> None:
    tool = make_snsf_grant_facets_tool(provider)
    assert tool.name == "snsf_grant_facets"
    result = _run(tool.function())
    assert isinstance(result, dict)
    assert "state" in result


def test_make_fetch_snsf_grant_tool_name_and_result(provider: SnsfGrantsProvider) -> None:
    tool = make_fetch_snsf_grant_tool(provider)
    assert tool.name == "fetch_snsf_grant"

    row = _run(tool.function(grant_number=_G2))
    assert row is not None
    assert row["grant_number"] == _G2
    assert "abstract" in row


def test_make_fetch_snsf_grant_tool_missing(provider: SnsfGrantsProvider) -> None:
    tool = make_fetch_snsf_grant_tool(provider)
    row = _run(tool.function(grant_number="https://data.snf.ch/grants/grant/999999"))
    assert row is None
