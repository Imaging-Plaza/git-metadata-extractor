"""Phase A: build_facets() — derived facet tables for the SNSF index.

Tests cover:
- Correct flattening of persons JSON grant arrays into grant_persons rows.
- Correct grant_output_counts rollups (present + zero-output grants).
- Correct grant_countries rows.
- Idempotency: running build_facets twice yields the same counts, no duplicates.
- Bootstrap hook: bootstrap_all(only=["snsf"]) leaves a grant_persons table in
  the snsf DuckDB (schema is present even before any ingest data exists).
"""

from __future__ import annotations

import json

import duckdb
import pytest

from src.index._federated import bootstrap
from src.index.snsf.facets import build_facets
from src.index.snsf.storage.duckdb_store import SnsfStore

_BASE = "https://data.snf.ch/grants/grant/"
_G1 = f"{_BASE}100001"
_G2 = f"{_BASE}100002"

# Expected row counts used in multiple assertions
_EXPECTED_PERSONS_ROWS = 3
_EXPECTED_GRANTS = 2
_EXPECTED_PUB_COUNT = 2
_EXPECTED_COLLAB_COUNT = 1
_EXPECTED_COUNTRIES = 1


@pytest.fixture
def store(tmp_path):  # type: ignore[no-untyped-def]
    """Tiny SnsfStore with two grants, one person, some outputs."""
    s = SnsfStore.open(tmp_path / "snsf.duckdb")
    conn = s.connect()

    # Two grants
    conn.execute("INSERT INTO grants (grant_number, title) VALUES (?, ?)", [_G1, "Grant one"])
    conn.execute("INSERT INTO grants (grant_number, title) VALUES (?, ?)", [_G2, "Grant two"])

    # One person attached to both grants in different roles
    conn.execute(
        "INSERT INTO persons (person_number, responsible_applicant_grants, co_applicant_grants) "
        "VALUES (?, ?, ?)",
        [42, json.dumps([_G1, _G2]), json.dumps([_G2])],
    )

    # Publications: 2 for G1, 0 for G2
    conn.execute(
        "INSERT INTO output_publications (publication_id, grant_number) VALUES ('pub1', ?)",
        [_G1],
    )
    conn.execute(
        "INSERT INTO output_publications (publication_id, grant_number) VALUES ('pub2', ?)",
        [_G1],
    )

    # Collaboration with a country for G1
    conn.execute(
        "INSERT INTO output_collaborations (collaboration_id, grant_number, country) "
        "VALUES ('col1', ?, 'Germany')",
        [_G1],
    )

    yield s
    s.close()


# ---------------------------------------------------------------------------
# Core build_facets tests
# ---------------------------------------------------------------------------


def test_build_facets_returns_counts(store: SnsfStore) -> None:
    counts = build_facets(store)
    assert isinstance(counts, dict)
    assert "grant_persons" in counts
    assert "grant_output_counts" in counts
    assert "grant_countries" in counts


def test_grant_persons_flattened(store: SnsfStore) -> None:
    build_facets(store)
    conn = store.connect()
    rows = conn.execute(
        "SELECT grant_number, person_number, role FROM grant_persons ORDER BY grant_number, role",
    ).fetchall()
    # person 42 is responsible_applicant for G1 + G2, and co_applicant for G2
    assert (f"{_BASE}100001", 42, "responsible_applicant") in rows
    assert (f"{_BASE}100002", 42, "responsible_applicant") in rows
    assert (f"{_BASE}100002", 42, "co_applicant") in rows
    assert len(rows) == _EXPECTED_PERSONS_ROWS


def test_grant_persons_count_returned(store: SnsfStore) -> None:
    counts = build_facets(store)
    assert counts["grant_persons"] == _EXPECTED_PERSONS_ROWS


def test_grant_output_counts_for_g1(store: SnsfStore) -> None:
    build_facets(store)
    conn = store.connect()
    row = conn.execute(
        "SELECT n_publications, n_collaborations, n_datasets "
        "FROM grant_output_counts WHERE grant_number = ?",
        [_G1],
    ).fetchone()
    assert row is not None
    n_publications, n_collaborations, n_datasets = row
    assert n_publications == _EXPECTED_PUB_COUNT
    assert n_collaborations == _EXPECTED_COLLAB_COUNT
    assert n_datasets == 0


def test_grant_output_counts_zero_for_g2(store: SnsfStore) -> None:
    build_facets(store)
    conn = store.connect()
    row = conn.execute(
        "SELECT n_publications FROM grant_output_counts WHERE grant_number = ?",
        [_G2],
    ).fetchone()
    assert row is not None
    assert row[0] == 0


def test_grant_output_counts_count_returned(store: SnsfStore) -> None:
    counts = build_facets(store)
    # One row per grant
    assert counts["grant_output_counts"] == _EXPECTED_GRANTS


def test_grant_countries(store: SnsfStore) -> None:
    build_facets(store)
    conn = store.connect()
    rows = conn.execute(
        "SELECT grant_number, country FROM grant_countries",
    ).fetchall()
    assert (f"{_BASE}100001", "Germany") in rows
    assert len(rows) == _EXPECTED_COUNTRIES


def test_grant_countries_count_returned(store: SnsfStore) -> None:
    counts = build_facets(store)
    assert counts["grant_countries"] == _EXPECTED_COUNTRIES


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_build_facets_idempotent(store: SnsfStore) -> None:
    counts1 = build_facets(store)
    counts2 = build_facets(store)
    assert counts1 == counts2

    conn = store.connect()
    # No duplicate rows in grant_persons
    total = conn.execute("SELECT count(*) FROM grant_persons").fetchone()[0]
    assert total == _EXPECTED_PERSONS_ROWS

    # No duplicate rows in grant_countries
    total_c = conn.execute("SELECT count(*) FROM grant_countries").fetchone()[0]
    assert total_c == _EXPECTED_COUNTRIES


# ---------------------------------------------------------------------------
# Bootstrap hook
# ---------------------------------------------------------------------------


def test_bootstrap_hook_creates_facet_tables(
    tmp_path,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """bootstrap_all(only=["snsf"]) must create the grant_persons table (and
    siblings) in the snsf DuckDB, even before any ingest data is present."""
    snsf_dir = tmp_path / "snsf" / "duckdb"
    snsf_dir.mkdir(parents=True)
    db_file = snsf_dir / "snsf.duckdb"

    monkeypatch.setenv("INDEX_DATA_DIR", str(tmp_path))

    result = bootstrap.bootstrap_all(only=["snsf"])
    status = result.get("snsf", "")
    assert not status.startswith("error"), f"bootstrap error: {status}"

    # The DuckDB must now have the grant_persons table.
    assert db_file.exists(), "DuckDB not created"
    conn = duckdb.connect(str(db_file), read_only=True)
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'",
        ).fetchall()
    }
    conn.close()
    assert "grant_persons" in tables, f"grant_persons missing; found: {tables}"
    assert "grant_output_counts" in tables
    assert "grant_countries" in tables
