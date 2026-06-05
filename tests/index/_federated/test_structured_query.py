"""Phase C — structured_query federated surface.

Tests verify:
- structured_query_capable() includes 'snsf'.
- run_structured_query('snsf', GrantFilters(...)) returns the query_grants shape
  (total + results) using a tmp store.
- The manifest entry for snsf has structured_query == True.
- run_structured_query on an index without facet_query raises a clear error.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.index.snsf.facet_query import GrantFilters
from src.index.snsf.facets import build_facets
from src.index.snsf.storage.duckdb_store import SnsfStore

_BASE = "https://data.snf.ch/grants/grant/"
_G1 = f"{_BASE}500001"
_G2 = f"{_BASE}500002"


@pytest.fixture
def snsf_store(tmp_path: Path) -> Path:
    """Seed a tiny store, return its db_path."""
    db_path = tmp_path / "snsf_sq.duckdb"
    s = SnsfStore.open(db_path)
    conn = s.connect()

    conn.execute(
        "INSERT INTO grants "
        "(grant_number, title, title_english, abstract, keywords, "
        " funding_instrument, research_institution, state, main_discipline, "
        " main_field_of_research, call_decision_year, start_date, end_date, amount_granted) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            _G1, "Structured Query Test 1", "SQ Test 1 EN",
            "Abstract A.", "sq; test",
            "ProjectFunding", "EPF Lausanne - EPFL", "Active",
            "Biology", "Life Sciences", 2021,
            "2021-01-01", "2023-12-31", 300_000,
        ],
    )
    conn.execute(
        "INSERT INTO grants "
        "(grant_number, title, title_english, abstract, keywords, "
        " funding_instrument, research_institution, state, main_discipline, "
        " main_field_of_research, call_decision_year, start_date, end_date, amount_granted) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            _G2, "Structured Query Test 2", "SQ Test 2 EN",
            "Abstract B.", "sq; other",
            "Ambizione", "ETH Zurich", "Completed",
            "Physics", "Natural Sciences", 2022,
            "2022-06-01", "2024-05-31", 150_000,
        ],
    )
    build_facets(s)
    s.close()
    return db_path


# ---------------------------------------------------------------------------
# structured_query_capable
# ---------------------------------------------------------------------------


def test_structured_query_capable_includes_snsf() -> None:
    from src.index._federated.structured_query import (  # noqa: PLC0415
        structured_query_capable,
    )
    capable = structured_query_capable()
    assert "snsf" in capable


# ---------------------------------------------------------------------------
# run_structured_query
# ---------------------------------------------------------------------------


def _make_patched_facet_query(
    snsf_store_path: Path,
) -> Any:
    """Return a patched facet_query bound to the tmp store path."""
    def _patched(_self: Any, filters: Any, *, text: str | None = None, sort: str = "start_date_desc", limit: int = 50, offset: int = 0) -> dict[str, Any]:
        from src.index.snsf.facet_query import query_grants  # noqa: PLC0415
        store = SnsfStore.open(snsf_store_path)
        try:
            return query_grants(store, filters, text=text, sort=sort, limit=limit, offset=offset)
        finally:
            store.close()
    return _patched


def test_run_structured_query_returns_query_grants_shape(
    monkeypatch: pytest.MonkeyPatch,
    snsf_store: Path,
) -> None:
    import src.index._federated.adapters.snsf as snsf_mod  # noqa: PLC0415
    from src.index._federated.structured_query import (  # noqa: PLC0415
        run_structured_query,
    )

    monkeypatch.setattr(snsf_mod.SnsfAdapter, "facet_query", _make_patched_facet_query(snsf_store))

    result = run_structured_query("snsf", GrantFilters())
    assert "total" in result
    assert "results" in result
    assert result["total"] == 2  # noqa: PLR2004


def test_run_structured_query_with_filter(
    monkeypatch: pytest.MonkeyPatch,
    snsf_store: Path,
) -> None:
    import src.index._federated.adapters.snsf as snsf_mod  # noqa: PLC0415
    from src.index._federated.structured_query import (  # noqa: PLC0415
        run_structured_query,
    )

    monkeypatch.setattr(snsf_mod.SnsfAdapter, "facet_query", _make_patched_facet_query(snsf_store))

    result = run_structured_query("snsf", GrantFilters(state=["Active"]))
    assert result["total"] == 1
    assert result["results"][0]["state"] == "Active"


def test_run_structured_query_unknown_index_raises() -> None:
    from src.index._federated.structured_query import (  # noqa: PLC0415
        run_structured_query,
    )
    with pytest.raises(ValueError, match="no facet_query"):
        run_structured_query("openalex", GrantFilters())


# ---------------------------------------------------------------------------
# Manifest entry has structured_query == True for snsf
# ---------------------------------------------------------------------------


def test_manifest_entry_snsf_has_structured_query() -> None:
    from src.index._federated.manifest import build_manifest  # noqa: PLC0415
    by_name = {e["name"]: e for e in build_manifest()}
    snsf_entry = by_name.get("snsf")
    assert snsf_entry is not None, "snsf missing from manifest"
    assert snsf_entry.get("structured_query") is True


def test_manifest_entry_other_index_structured_query_false() -> None:
    from src.index._federated.manifest import build_manifest  # noqa: PLC0415
    by_name = {e["name"]: e for e in build_manifest()}
    # openalex has no facet_query → should default to False
    oa_entry = by_name.get("openalex")
    assert oa_entry is not None
    assert oa_entry.get("structured_query") is False


def test_snsf_adapter_has_structured_query_attr() -> None:
    """The SnsfAdapter class must declare structured_query = True."""
    import src.index._federated.adapters.snsf as snsf_mod  # noqa: PLC0415
    adapter = snsf_mod.SnsfAdapter()
    assert getattr(adapter, "structured_query", False) is True


def test_snsf_adapter_has_facet_query_method() -> None:
    import src.index._federated.adapters.snsf as snsf_mod  # noqa: PLC0415
    adapter = snsf_mod.SnsfAdapter()
    assert callable(getattr(adapter, "facet_query", None))
