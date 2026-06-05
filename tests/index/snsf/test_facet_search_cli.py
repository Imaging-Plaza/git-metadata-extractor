"""Phase C — CLI `facet-search` subcommand for the SNSF index.

Tests verify:
- `facet-search --status completed --limit 5` → valid JSON with total/results.
- `facet-search --status completed --facets` → adds `facets` to the output.
- Multiple repeatable args (--scheme, --institution, etc.) are accepted.
- Empty-store returns total=0, results=[].
"""

from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path

import pytest

from src.index.snsf.facets import build_facets
from src.index.snsf.storage.duckdb_store import SnsfStore

_BASE = "https://data.snf.ch/grants/grant/"
_G1 = f"{_BASE}300001"
_G2 = f"{_BASE}300002"


def _seed_store(db_path: Path) -> SnsfStore:
    """Create a tiny store with 2 grants and build facets."""
    s = SnsfStore.open(db_path)
    conn = s.connect()

    conn.execute(
        "INSERT INTO grants "
        "(grant_number, title, title_english, abstract, keywords, "
        " funding_instrument, research_institution, state, main_discipline, "
        " main_field_of_research, call_decision_year, start_date, end_date, amount_granted) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            _G1, "Alpha Project", "Alpha Project EN",
            "Abstract for alpha.", "alpha; beta",
            "ProjectFunding", "EPF Lausanne - EPFL", "Completed",
            "Biology", "Life Sciences", 2021,
            "2021-01-01", "2023-12-31", 400_000,
        ],
    )

    conn.execute(
        "INSERT INTO grants "
        "(grant_number, title, title_english, abstract, keywords, "
        " funding_instrument, research_institution, state, main_discipline, "
        " main_field_of_research, call_decision_year, start_date, end_date, amount_granted) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            _G2, "Beta Project", "Beta Project EN",
            "Abstract for beta.", "gamma; delta",
            "Ambizione", "ETH Zurich", "Active",
            "Physics", "Natural Sciences", 2022,
            "2022-06-01", "2024-05-31", 200_000,
        ],
    )

    build_facets(s)
    return s


def _make_patched_store_class(db_path: Path) -> type:
    """Return a SnsfStore subclass whose .open() always uses db_path."""
    class _PatchedStore(SnsfStore):
        @classmethod
        def open(cls, _dp: Path | None = None) -> SnsfStore:  # type: ignore[override]
            return SnsfStore.open(db_path)
    return _PatchedStore


def _run_cli(argv: list[str], *, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict:  # type: ignore[type-arg]
    """Monkeypatch SnsfStore, run cli.main(), capture stdout, return parsed JSON."""
    db_path = tmp_path / "snsf_cli.duckdb"
    store = _seed_store(db_path)
    store.close()

    monkeypatch.setattr("src.index.snsf.cli.SnsfStore", _make_patched_store_class(db_path))

    captured = StringIO()
    original_stdout = sys.stdout
    sys.stdout = captured
    try:
        from src.index.snsf import cli  # noqa: PLC0415
        rc = cli.main(argv)
    finally:
        sys.stdout = original_stdout

    assert rc == 0, f"CLI exited with {rc}"
    output = captured.getvalue().strip()
    return json.loads(output)  # type: ignore[return-value]


def test_facet_search_no_filters(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    result = _run_cli(["facet-search", "--limit", "10"], monkeypatch=monkeypatch, tmp_path=tmp_path)
    assert "total" in result
    assert "results" in result
    assert result["total"] == 2  # noqa: PLR2004
    assert len(result["results"]) == 2  # noqa: PLR2004


def test_facet_search_status_filter(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    result = _run_cli(
        ["facet-search", "--status", "Completed", "--limit", "5"],
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )
    assert result["total"] == 1
    assert len(result["results"]) == 1
    assert result["results"][0]["state"] == "Completed"


def test_facet_search_no_facets_key_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    result = _run_cli(
        ["facet-search", "--limit", "5"],
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )
    assert "facets" not in result


def test_facet_search_with_facets_flag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    result = _run_cli(
        ["facet-search", "--limit", "5", "--facets"],
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )
    assert "facets" in result
    facets = result["facets"]
    assert isinstance(facets, dict)
    assert "funding_instrument" in facets
    assert "state" in facets


def test_facet_search_scheme_filter(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    result = _run_cli(
        ["facet-search", "--scheme", "ProjectFunding"],
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )
    assert result["total"] == 1
    assert result["results"][0]["funding_instrument"] == "ProjectFunding"


def test_facet_search_multiple_status(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    result = _run_cli(
        ["facet-search", "--status", "Completed", "--status", "Active"],
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )
    assert result["total"] == 2  # noqa: PLR2004


def test_facet_search_text_query(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    result = _run_cli(
        ["facet-search", "--q", "alpha"],
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )
    assert result["total"] == 1


def test_facet_search_limit_and_offset(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    result = _run_cli(
        ["facet-search", "--limit", "1", "--offset", "0"],
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )
    assert result["total"] == 2  # noqa: PLR2004
    assert len(result["results"]) == 1


def test_facet_search_empty_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An empty store returns total=0, results=[]."""
    empty_db = tmp_path / "empty_snsf.duckdb"
    empty_store = SnsfStore.open(empty_db)
    empty_store.close()

    monkeypatch.setattr("src.index.snsf.cli.SnsfStore", _make_patched_store_class(empty_db))

    captured = StringIO()
    original_stdout = sys.stdout
    sys.stdout = captured
    try:
        from src.index.snsf import cli  # noqa: PLC0415
        rc = cli.main(["facet-search", "--limit", "5"])
    finally:
        sys.stdout = original_stdout

    assert rc == 0
    result = json.loads(captured.getvalue().strip())
    assert result["total"] == 0
    assert result["results"] == []
