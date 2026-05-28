"""Tests for the cross-catalog DOI canonical-URL helper."""

from __future__ import annotations

import duckdb
import pytest

from src.index._shared.doi import (
    doi_iri,
    migrate_doi_column_to_url,
    parse_doi,
)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_doi_iri_promotes_bare_doi():
    assert doi_iri("10.5281/zenodo.18314844") == (
        "https://doi.org/10.5281/zenodo.18314844"
    )


def test_doi_iri_is_idempotent():
    iri = "https://doi.org/10.5281/zenodo.18314844"
    assert doi_iri(iri) == iri
    assert doi_iri(iri + "/") == iri


def test_doi_iri_normalises_dx_doi_org():
    assert doi_iri("https://dx.doi.org/10.1234/abcd") == (
        "https://doi.org/10.1234/abcd"
    )


def test_doi_iri_strips_doi_scheme_prefix():
    assert doi_iri("doi:10.1234/abcd") == "https://doi.org/10.1234/abcd"
    assert doi_iri("DOI:10.1234/abcd") == "https://doi.org/10.1234/abcd"


def test_doi_iri_handles_empty_and_none():
    assert doi_iri(None) is None
    assert doi_iri("") is None
    assert doi_iri("   ") is None


def test_parse_doi_round_trip():
    assert parse_doi("https://doi.org/10.5281/zenodo.123") == "10.5281/zenodo.123"
    assert parse_doi("https://dx.doi.org/10.5281/zenodo.123") == "10.5281/zenodo.123"
    assert parse_doi("doi:10.1234/abcd") == "10.1234/abcd"
    assert parse_doi("10.1234/abcd") == "10.1234/abcd"
    assert parse_doi(None) is None
    assert parse_doi("") is None


# ---------------------------------------------------------------------------
# Migration helper
# ---------------------------------------------------------------------------


def test_migrate_doi_column_to_url_rewrites_bare_and_dx_rows(tmp_path):
    db = tmp_path / "test.duckdb"
    conn = duckdb.connect(str(db))
    conn.execute("CREATE TABLE pubs (id INTEGER PRIMARY KEY, doi TEXT)")
    conn.executemany(
        "INSERT INTO pubs VALUES (?, ?)",
        [
            (1, "10.5281/zenodo.123"),
            (2, "https://dx.doi.org/10.1234/abcd"),
            (3, "https://doi.org/10.5281/zenodo.999"),  # already canonical
            (4, "doi:10.4567/xyz"),
            (5, None),  # NULL stays NULL
        ],
    )

    rewritten = migrate_doi_column_to_url(conn, table="pubs", column="doi")
    # 3 rewrites: bare (1), dx-host (2), doi:-prefixed (4). Row 3 is
    # already canonical and row 5 is NULL — neither needs touching.
    assert rewritten == 3

    final = dict(conn.execute("SELECT id, doi FROM pubs ORDER BY id").fetchall())
    assert final[1] == "https://doi.org/10.5281/zenodo.123"
    assert final[2] == "https://doi.org/10.1234/abcd"
    assert final[3] == "https://doi.org/10.5281/zenodo.999"  # untouched
    assert final[4] == "https://doi.org/10.4567/xyz"
    assert final[5] is None
    conn.close()


def test_migrate_doi_column_to_url_is_idempotent(tmp_path):
    db = tmp_path / "test.duckdb"
    conn = duckdb.connect(str(db))
    conn.execute("CREATE TABLE pubs (id INTEGER PRIMARY KEY, doi TEXT)")
    conn.execute("INSERT INTO pubs VALUES (1, '10.5281/zenodo.123')")

    first = migrate_doi_column_to_url(conn, table="pubs", column="doi")
    second = migrate_doi_column_to_url(conn, table="pubs", column="doi")
    third = migrate_doi_column_to_url(conn, table="pubs", column="doi")
    assert first == 1
    assert second == 0
    assert third == 0

    final = conn.execute("SELECT doi FROM pubs WHERE id = 1").fetchone()[0]
    assert final == "https://doi.org/10.5281/zenodo.123"
    conn.close()


def test_migrate_skips_empty_column():
    """Empty table → 0 rows rewritten, no error."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "test.duckdb"
        conn = duckdb.connect(str(db))
        conn.execute("CREATE TABLE pubs (id INTEGER PRIMARY KEY, doi TEXT)")
        rewritten = migrate_doi_column_to_url(conn, table="pubs", column="doi")
        conn.close()
    assert rewritten == 0
