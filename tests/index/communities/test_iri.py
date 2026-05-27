"""Tests for the canonical-IRI helper + the legacy-row migration."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from src.index.communities.iri import (
    UnknownCommunitySource,
    canonical_community_id,
)


def test_canonical_community_id_zenodo_format():
    assert canonical_community_id("zenodo", "epfl") == (
        "https://zenodo.org/communities/epfl"
    )
    # No trailing slash — matches the rest of the codebase's IRI hygiene.
    iri = canonical_community_id("zenodo", "eesd_at_epfl")
    assert iri == "https://zenodo.org/communities/eesd_at_epfl"
    assert not iri.endswith("/")


def test_canonical_community_id_rejects_unknown_source():
    with pytest.raises(UnknownCommunitySource):
        canonical_community_id("flickr", "whatever")


def _run_schema(db_path: Path) -> None:
    """Bootstrap the schema (statement-by-statement, like the store does)."""
    schema = (
        Path(__file__).resolve().parents[3]
        / "src" / "index" / "communities" / "storage" / "schema.sql"
    ).read_text(encoding="utf-8")
    conn = duckdb.connect(str(db_path))
    for stmt in [s.strip() for s in schema.split(";") if s.strip()]:
        conn.execute(stmt + ";")
    conn.close()


def test_legacy_zenodo_rows_are_migrated_on_bootstrap(tmp_path: Path):
    """Pre-seed a DB with the old `zenodo:<slug>` rows, re-bootstrap, and
    confirm every row now carries the canonical IRI.
    """
    db_path = tmp_path / "communities.duckdb"
    # First bootstrap creates the table.
    _run_schema(db_path)

    # Seed legacy rows + one that's already in the new format (idempotency).
    conn = duckdb.connect(str(db_path))
    conn.execute(
        "INSERT INTO communities (community_id, source, source_slug) VALUES "
        "('zenodo:epfl', 'zenodo', 'epfl'), "
        "('zenodo:cern', 'zenodo', 'cern'), "
        "('https://zenodo.org/communities/already-migrated', 'zenodo', 'already-migrated')",
    )
    conn.close()

    # Re-bootstrap → UPDATE clause rewrites the legacy rows in-place.
    _run_schema(db_path)

    conn = duckdb.connect(str(db_path), read_only=True)
    ids = sorted(
        row[0]
        for row in conn.execute("SELECT community_id FROM communities").fetchall()
    )
    conn.close()

    assert ids == [
        "https://zenodo.org/communities/already-migrated",
        "https://zenodo.org/communities/cern",
        "https://zenodo.org/communities/epfl",
    ]


def test_migration_is_idempotent(tmp_path: Path):
    """Bootstrap twice on the same DB: row count and ids must be unchanged."""
    db_path = tmp_path / "communities.duckdb"
    _run_schema(db_path)
    conn = duckdb.connect(str(db_path))
    conn.execute(
        "INSERT INTO communities (community_id, source, source_slug) VALUES "
        "('zenodo:foo', 'zenodo', 'foo')",
    )
    conn.close()

    for _ in range(3):
        _run_schema(db_path)

    conn = duckdb.connect(str(db_path), read_only=True)
    rows = conn.execute("SELECT community_id FROM communities").fetchall()
    conn.close()
    assert rows == [("https://zenodo.org/communities/foo",)]
