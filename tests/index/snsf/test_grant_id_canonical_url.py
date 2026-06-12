"""v3.0.0: SNSF grant ids (PK + FKs + persons JSON arrays) are canonical URLs.

Covers the riskiest pieces: the bulk-CSV load SQL fragments, `fetch_grant`
accepting any input shape, and the in-place INTEGER→URL bootstrap migration.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from src.index.snsf.storage.duckdb_store import (
    _GRANT_URL_SQL,
    SnsfStore,
)

_URL = "https://data.snf.ch/grants/grant/241892"


@pytest.fixture()
def store(tmp_path: Path) -> SnsfStore:
    s = SnsfStore.open(tmp_path / "snsf.duckdb")
    yield s
    s.close()


def test_schema_grant_number_is_text(store: SnsfStore) -> None:
    row = store.connect().execute(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name = 'grants' AND column_name = 'grant_number'",
    ).fetchone()
    assert "INT" not in str(row[0]).upper()


def test_fetch_grant_accepts_int_str_and_url(store: SnsfStore) -> None:
    store.connect().execute(
        "INSERT INTO grants (grant_number, title) VALUES (?, ?)",
        [_URL, "A grant"],
    )
    for key in (241892, "241892", _URL):
        row = store.fetch_grant(key)
        assert row is not None, key
        assert row["grant_number"] == _URL


def test_grant_url_sql_fragment_behaviour() -> None:
    """The shared SQL fragment URL-ifies a bare integer, passes URLs/NULL."""
    c = duckdb.connect()
    c.execute("CREATE TABLE t (id INTEGER, GrantNumber VARCHAR)")
    c.execute(
        "INSERT INTO t VALUES (1, '241892'), (2, ?), (3, NULL)", [_URL],
    )
    got = [
        r[0]
        for r in c.execute(f"SELECT {_GRANT_URL_SQL} FROM t ORDER BY id").fetchall()
    ]
    assert got == [_URL, _URL, None]


def test_split_fragment_builds_url_arrays() -> None:
    """The persons `_split` lambda turns ';'-joined grant numbers into a JSON
    array of grant URLs (non-numeric tokens -> NULL)."""
    base = "https://data.snf.ch/grants/grant/"
    c = duckdb.connect()
    expr = (
        "TO_JSON(LIST_TRANSFORM(STRING_SPLIT(CAST(col AS VARCHAR), ';'), "
        f"x -> CASE WHEN regexp_full_match(TRIM(x), '\\d+') "
        f"THEN '{base}' || TRIM(x) ELSE NULL END))"
    )
    c.execute("CREATE TABLE p (col VARCHAR)")
    c.execute("INSERT INTO p VALUES ('108806;125710')")
    out = c.execute(f"SELECT {expr} FROM p").fetchone()[0]
    assert f"{base}108806" in out
    assert f"{base}125710" in out


def test_migration_promotes_legacy_integer_ids(tmp_path: Path) -> None:
    """A pre-v3 DB (grant_number INTEGER, JSON int arrays) is migrated in
    place to URLs, idempotently. Exercises `_migrate_grant_ids_to_url`
    directly on the columns it touches (the full schema's indexes are
    covered elsewhere)."""
    conn = duckdb.connect(str(tmp_path / "legacy.duckdb"))
    conn.execute("CREATE TABLE grants (grant_number INTEGER PRIMARY KEY, title TEXT)")
    conn.execute("INSERT INTO grants VALUES (241892, 'A'), (999, 'B')")
    conn.execute("CREATE TABLE output_publications (publication_id TEXT, grant_number INTEGER)")
    conn.execute("INSERT INTO output_publications VALUES ('p1', 241892)")
    conn.execute("CREATE TABLE scope_records (scope_mode TEXT, grant_number INTEGER)")
    conn.execute("INSERT INTO scope_records VALUES ('epfl', 241892)")
    # Include the columns schema.sql indexes (orcid, research_institution) so
    # the migration's schema re-run can (re)create the persons indexes.
    conn.execute(
        "CREATE TABLE persons (person_number INTEGER, orcid TEXT, "
        "research_institution TEXT, "
        "responsible_applicant_grants JSON, co_applicant_grants JSON, "
        "project_partner_grants JSON, practice_partner_grants JSON, "
        "employee_grants JSON, contact_person_grants JSON, "
        "applicant_abroad_grants JSON)",
    )
    conn.execute(
        "INSERT INTO persons VALUES (1, NULL, NULL, TO_JSON([241892, 999]), "
        "NULL, NULL, NULL, NULL, NULL, NULL)",
    )

    SnsfStore._migrate_grant_ids_to_url(conn)

    assert conn.execute("SELECT grant_number FROM grants ORDER BY title").fetchall() == [
        ("https://data.snf.ch/grants/grant/241892",),
        ("https://data.snf.ch/grants/grant/999",),
    ]
    assert conn.execute("SELECT grant_number FROM output_publications").fetchone()[0] == _URL
    assert conn.execute("SELECT grant_number FROM scope_records").fetchone()[0] == _URL
    arr = conn.execute("SELECT responsible_applicant_grants FROM persons").fetchone()[0]
    assert _URL in arr and "https://data.snf.ch/grants/grant/999" in arr

    # Idempotent: re-running detects the already-migrated (VARCHAR) schema
    # and changes nothing.
    SnsfStore._migrate_grant_ids_to_url(conn)
    assert conn.execute("SELECT grant_number FROM grants WHERE title = 'A'").fetchone()[0] == _URL
    conn.close()


def test_migration_persons_array_tolerates_url_null_and_nonnumeric(tmp_path: Path) -> None:
    """Bug 12: a partially-migrated / mixed persons array (already-URL element,
    bare int, non-numeric token, null) must migrate without raising. The old
    `CAST(<col> AS BIGINT[])` threw a Conversion Error here; the VARCHAR-keyed
    transform passes URLs through, promotes ints, and drops null/non-numeric."""
    import json

    conn = duckdb.connect(str(tmp_path / "mixed.duckdb"))
    conn.execute("CREATE TABLE grants (grant_number INTEGER PRIMARY KEY, title TEXT)")
    conn.execute("INSERT INTO grants VALUES (241892, 'A')")
    conn.execute("CREATE TABLE output_publications (publication_id TEXT, grant_number INTEGER)")
    conn.execute("INSERT INTO output_publications VALUES ('p1', 241892)")
    conn.execute("CREATE TABLE scope_records (scope_mode TEXT, grant_number INTEGER)")
    conn.execute("INSERT INTO scope_records VALUES ('epfl', 241892)")
    conn.execute(
        "CREATE TABLE persons (person_number INTEGER, orcid TEXT, "
        "research_institution TEXT, "
        "responsible_applicant_grants JSON, co_applicant_grants JSON, "
        "project_partner_grants JSON, practice_partner_grants JSON, "
        "employee_grants JSON, contact_person_grants JSON, "
        "applicant_abroad_grants JSON)",
    )
    # Raw JSON literal so the array can legitimately hold mixed types (string
    # URL, number, non-numeric token, json null) the way a partially-migrated
    # DB would. A DuckDB list literal can't mix those types.
    conn.execute(
        "INSERT INTO persons VALUES (1, NULL, NULL, "
        "'[\"https://data.snf.ch/grants/grant/241892\", 999, \"abc\", null]'::JSON, "
        "NULL, NULL, NULL, NULL, NULL, NULL)",
    )

    SnsfStore._migrate_grant_ids_to_url(conn)  # must not raise

    arr = json.loads(
        conn.execute("SELECT responsible_applicant_grants FROM persons").fetchone()[0],
    )
    assert arr == [
        "https://data.snf.ch/grants/grant/241892",  # already-URL → passed through
        "https://data.snf.ch/grants/grant/999",     # bare int → promoted
    ]  # 'abc' and null dropped
    conn.close()
