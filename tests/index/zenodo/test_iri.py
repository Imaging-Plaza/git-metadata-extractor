"""Tests for Zenodo IRI helpers + the bootstrap CTAS-swap migration."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from src.index.zenodo.iri import (
    community_iri,
    doi_iri,
    parse_community_slug,
    parse_doi,
    parse_record_id,
    record_iri,
)
from src.index.zenodo.storage.duckdb_store import ZenodoStore


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_record_iri_promotes_bare_id():
    assert record_iri("18314844") == "https://zenodo.org/records/18314844"


def test_record_iri_idempotent_on_iri_input():
    iri = "https://zenodo.org/records/18314844"
    assert record_iri(iri) == iri
    # Trailing slash stripped for hygiene.
    assert record_iri(iri + "/") == iri


def test_community_iri_promotes_bare_slug():
    assert community_iri("epfl") == "https://zenodo.org/communities/epfl"


def test_community_iri_idempotent():
    iri = "https://zenodo.org/communities/eesd_at_epfl"
    assert community_iri(iri) == iri
    assert community_iri(iri + "/") == iri


def test_parse_round_trip():
    assert parse_record_id("https://zenodo.org/records/123") == "123"
    assert parse_record_id("123") == "123"
    assert parse_record_id("") is None
    assert parse_community_slug("https://zenodo.org/communities/epfl") == "epfl"
    assert parse_community_slug("epfl") == "epfl"


def test_doi_iri_promotes_bare_doi():
    assert doi_iri("10.5281/zenodo.18314844") == (
        "https://doi.org/10.5281/zenodo.18314844"
    )


def test_doi_iri_is_idempotent_and_strips_trailing_slash():
    iri = "https://doi.org/10.5281/zenodo.18314844"
    assert doi_iri(iri) == iri
    assert doi_iri(iri + "/") == iri


def test_doi_iri_normalises_dx_doi_org_legacy_host():
    """Older ingest paths sometimes emit `https://dx.doi.org/…` — promote
    to the canonical `https://doi.org/…` host.
    """
    legacy = "https://dx.doi.org/10.1234/abcd"
    assert doi_iri(legacy) == "https://doi.org/10.1234/abcd"


def test_doi_iri_strips_doi_scheme_prefix():
    assert doi_iri("doi:10.1234/abcd") == "https://doi.org/10.1234/abcd"
    assert doi_iri("DOI:10.1234/abcd") == "https://doi.org/10.1234/abcd"


def test_parse_doi_round_trip():
    assert parse_doi("https://doi.org/10.5281/zenodo.123") == "10.5281/zenodo.123"
    assert parse_doi("https://dx.doi.org/10.5281/zenodo.123") == "10.5281/zenodo.123"
    assert parse_doi("doi:10.1234/abcd") == "10.1234/abcd"
    assert parse_doi("10.1234/abcd") == "10.1234/abcd"
    assert parse_doi("") is None


def test_bootstrap_migrates_dois_to_url_form(tmp_path: Path):
    """Pre-PR rows carry bare DOI / legacy dx.doi.org host → bootstrap
    rewrites both to the canonical `https://doi.org/…` form.
    """
    db_path = tmp_path / "zenodo.duckdb"
    # Pre-PR table shape: original columns only (no concept_doi yet —
    # exercise the case where the new ALTER + DOI migration both fire
    # in the same bootstrap pass).
    conn = duckdb.connect(str(db_path))
    conn.execute(
        "CREATE TABLE records ("
        "  zenodo_id TEXT PRIMARY KEY, concept_recid TEXT, doi TEXT, "
        "  title TEXT, description TEXT, publication_date DATE, "
        "  resource_type TEXT, access_right TEXT, license_id TEXT, "
        "  keywords_json JSON, community_ids JSON, primary_community_id TEXT, "
        "  raw JSON, ingested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ")",
    )
    conn.executemany(
        "INSERT INTO records (zenodo_id, doi, title, raw) VALUES (?, ?, ?, ?)",
        [
            (
                "https://zenodo.org/records/A",
                "10.5281/zenodo.A",
                "bare",
                json.dumps({"id": "A", "conceptdoi": "10.5281/zenodo.X"}),
            ),
            (
                "https://zenodo.org/records/B",
                "https://dx.doi.org/10.5281/zenodo.B",
                "legacy dx host",
                json.dumps({"id": "B"}),
            ),
            (
                "https://zenodo.org/records/C",
                "https://doi.org/10.5281/zenodo.C",
                "already url",
                json.dumps({"id": "C"}),
            ),
            (
                "https://zenodo.org/records/D",
                None,
                "no doi at all",
                json.dumps({"id": "D"}),
            ),
        ],
    )
    # Tables the link-table migration touches must exist; empty is fine.
    for ddl in (
        "CREATE TABLE communities (community_id TEXT PRIMARY KEY)",
        "CREATE TABLE record_creators (record_id TEXT, creator_key TEXT, position INTEGER, PRIMARY KEY (record_id, creator_key))",
        "CREATE TABLE record_communities (record_id TEXT, community_id TEXT, PRIMARY KEY (record_id, community_id))",
        "CREATE TABLE files (record_id TEXT, file_key TEXT, file_id TEXT, size_bytes BIGINT, checksum TEXT, download_url TEXT, PRIMARY KEY (record_id, file_key))",
        "CREATE TABLE creators (creator_key TEXT PRIMARY KEY, display_name TEXT, orcid TEXT, affiliation TEXT, raw JSON, ingested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE chunks (chunk_id TEXT PRIMARY KEY, entity_type TEXT, entity_id TEXT, chunk_index INTEGER, text TEXT, token_count INTEGER, vector_id TEXT, embedded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)",
    ):
        conn.execute(ddl)
    conn.close()

    store = ZenodoStore(db_path)
    store.bootstrap()
    conn = store.connect()

    rows = {
        zid: (doi, cdoi)
        for zid, doi, cdoi in conn.execute(
            "SELECT zenodo_id, doi, concept_doi FROM records",
        ).fetchall()
    }
    # Every populated DOI is now the canonical URL form.
    assert rows["https://zenodo.org/records/A"][0] == "https://doi.org/10.5281/zenodo.A"
    assert rows["https://zenodo.org/records/B"][0] == "https://doi.org/10.5281/zenodo.B"
    assert rows["https://zenodo.org/records/C"][0] == "https://doi.org/10.5281/zenodo.C"
    # NULL DOIs stay NULL (no surprise rewrite).
    assert rows["https://zenodo.org/records/D"][0] is None
    # concept_doi backfilled from `raw` (record A had `conceptdoi`) and
    # is also in URL form.
    assert rows["https://zenodo.org/records/A"][1] == "https://doi.org/10.5281/zenodo.X"
    assert rows["https://zenodo.org/records/B"][1] is None
    store.close()


def test_doi_migration_is_idempotent(tmp_path: Path):
    """Bootstrap N times; URLs stay URLs and no double-prefixing happens."""
    db_path = tmp_path / "zenodo.duckdb"
    conn = duckdb.connect(str(db_path))
    schema = (
        Path(__file__).resolve().parents[3]
        / "src" / "index" / "zenodo" / "storage" / "schema.sql"
    ).read_text(encoding="utf-8")
    conn.execute(schema)
    conn.execute(
        "INSERT INTO records (zenodo_id, doi, concept_doi, title, raw) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            "https://zenodo.org/records/A",
            "10.5281/zenodo.A",  # bare on entry
            "10.5281/zenodo.X",
            "test",
            json.dumps({"id": "A"}),
        ],
    )
    conn.close()

    for _ in range(3):
        store = ZenodoStore(db_path)
        store.bootstrap()
        store.close()

    conn = duckdb.connect(str(db_path), read_only=True)
    doi, cdoi = conn.execute("SELECT doi, concept_doi FROM records").fetchone()
    conn.close()
    assert doi == "https://doi.org/10.5281/zenodo.A"
    assert cdoi == "https://doi.org/10.5281/zenodo.X"


# ---------------------------------------------------------------------------
# Bootstrap migration end-to-end
# ---------------------------------------------------------------------------


def _seed_legacy_db(db_path: Path) -> None:
    """Seed a DB with the pre-migration bare-id shape."""
    schema = (
        Path(__file__).resolve().parents[3]
        / "src" / "index" / "zenodo" / "storage" / "schema.sql"
    ).read_text(encoding="utf-8")
    conn = duckdb.connect(str(db_path))
    conn.execute(schema)
    conn.execute(
        "INSERT INTO records (zenodo_id, concept_recid, title, community_ids, primary_community_id) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            "18314844",
            "18314843",
            "Test record",
            json.dumps(["epfl", "eesd_at_epfl"]),
            "epfl",
        ],
    )
    conn.execute(
        "INSERT INTO records (zenodo_id, concept_recid, title, community_ids) "
        "VALUES (?, ?, ?, ?)",
        ["19845281", "6594482", "Another", json.dumps(["epfl"])],
    )
    conn.execute(
        "INSERT INTO communities (community_id, title) VALUES "
        "('epfl', 'EPFL'), ('eesd_at_epfl', 'EESD at EPFL')",
    )
    # Multiple creators per record to exercise the link table at depth.
    conn.executemany(
        "INSERT INTO record_creators VALUES (?, ?, ?)",
        [
            ("18314844", "https://orcid.org/0000-0002-1234-5678", 0),
            ("18314844", "name:foo-bar", 1),
            ("19845281", "https://orcid.org/0000-0003-9999-0000", 0),
        ],
    )
    conn.executemany(
        "INSERT INTO record_communities VALUES (?, ?)",
        [
            ("18314844", "epfl"),
            ("18314844", "eesd_at_epfl"),
            ("19845281", "epfl"),
        ],
    )
    conn.executemany(
        "INSERT INTO files (record_id, file_key, file_id) VALUES (?, ?, ?)",
        [
            ("18314844", "data.csv", "f1"),
            ("18314844", "readme.md", "f2"),
            ("19845281", "model.pkl", "f3"),
        ],
    )
    conn.executemany(
        "INSERT INTO chunks (chunk_id, entity_type, entity_id, chunk_index, text, token_count, vector_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("c1", "records", "18314844", 0, "chunk 1", 5, "v1"),
            ("c2", "records", "18314844", 1, "chunk 2", 5, "v2"),
            ("c3", "records", "19845281", 0, "chunk 3", 5, "v3"),
        ],
    )
    conn.close()


def test_bootstrap_migrates_every_pk_and_fk_in_lockstep(tmp_path: Path):
    db_path = tmp_path / "zenodo.duckdb"
    _seed_legacy_db(db_path)

    store = ZenodoStore(db_path)
    store.bootstrap()
    conn = store.connect()

    # All PKs / FKs carry IRIs.
    bare_records = conn.execute(
        "SELECT COUNT(*) FROM records WHERE zenodo_id NOT LIKE 'https://%'",
    ).fetchone()[0]
    bare_comms = conn.execute(
        "SELECT COUNT(*) FROM communities WHERE community_id NOT LIKE 'https://%'",
    ).fetchone()[0]
    bare_rc = conn.execute(
        "SELECT COUNT(*) FROM record_creators WHERE record_id NOT LIKE 'https://%'",
    ).fetchone()[0]
    bare_rcom = conn.execute(
        "SELECT COUNT(*) FROM record_communities WHERE record_id NOT LIKE 'https://%' "
        "OR community_id NOT LIKE 'https://%'",
    ).fetchone()[0]
    bare_files = conn.execute(
        "SELECT COUNT(*) FROM files WHERE record_id NOT LIKE 'https://%'",
    ).fetchone()[0]
    bare_chunks = conn.execute(
        "SELECT COUNT(*) FROM chunks "
        "WHERE entity_type = 'records' AND entity_id NOT LIKE 'https://%'",
    ).fetchone()[0]

    assert bare_records == 0
    assert bare_comms == 0
    assert bare_rc == 0
    assert bare_rcom == 0
    assert bare_files == 0
    assert bare_chunks == 0

    # primary_community_id + community_ids JSON also rewritten.
    pri = conn.execute(
        "SELECT primary_community_id FROM records WHERE zenodo_id = "
        "  'https://zenodo.org/records/18314844'",
    ).fetchone()[0]
    cids = conn.execute(
        "SELECT community_ids FROM records WHERE zenodo_id = "
        "  'https://zenodo.org/records/18314844'",
    ).fetchone()[0]
    assert pri == "https://zenodo.org/communities/epfl"
    parsed = json.loads(cids) if isinstance(cids, str) else cids
    assert parsed == [
        "https://zenodo.org/communities/epfl",
        "https://zenodo.org/communities/eesd_at_epfl",
    ]

    # Row counts preserved end-to-end.
    assert conn.execute("SELECT COUNT(*) FROM record_creators").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 3

    store.close()


def test_bootstrap_is_idempotent_after_migration(tmp_path: Path):
    db_path = tmp_path / "zenodo.duckdb"
    _seed_legacy_db(db_path)

    # First bootstrap does the work.
    store = ZenodoStore(db_path)
    store.bootstrap()
    store.close()

    # Subsequent bootstraps must be no-ops — no rows change.
    for _ in range(3):
        store = ZenodoStore(db_path)
        store.bootstrap()
        store.close()

    conn = duckdb.connect(str(db_path), read_only=True)
    # All the canonical-IRI invariants still hold.
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM record_creators "
            "WHERE record_id NOT LIKE 'https://%'",
        ).fetchone()[0]
        == 0
    )
    assert conn.execute("SELECT COUNT(*) FROM record_creators").fetchone()[0] == 3
    conn.close()


def test_bootstrap_backfills_stats_and_version_columns(tmp_path: Path):
    """Pre-PR DB without the new columns + an existing row with `raw` →
    bootstrap must ALTER the table, run the migration, and backfill every
    new column from the raw API payload.
    """
    db_path = tmp_path / "zenodo.duckdb"
    # Hand-craft a pre-migration table shape — only the original columns.
    conn = duckdb.connect(str(db_path))
    conn.execute(
        "CREATE TABLE records ("
        "  zenodo_id TEXT PRIMARY KEY, concept_recid TEXT, doi TEXT, "
        "  title TEXT, description TEXT, publication_date DATE, "
        "  resource_type TEXT, access_right TEXT, license_id TEXT, "
        "  keywords_json JSON, community_ids JSON, primary_community_id TEXT, "
        "  raw JSON, ingested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ")",
    )
    raw_payload = {
        "id": "18314844",
        "conceptrecid": "18314843",
        "conceptdoi": "10.5281/zenodo.18314843",
        "doi": "10.5281/zenodo.18314844",
        "revision": 5,
        "created": "2024-01-15T10:30:00+00:00",
        "updated": "2026-02-20T09:00:00+00:00",
        "metadata": {"title": "Test", "version": "v2.1"},
        "stats": {
            "views": 4242,
            "unique_views": 3000,
            "downloads": 128,
            "unique_downloads": 100,
            "version_views": 1000,
            "version_unique_views": 750,
            "version_downloads": 30,
            "version_unique_downloads": 28,
        },
    }
    conn.execute(
        "INSERT INTO records (zenodo_id, concept_recid, doi, title, raw) "
        "VALUES (?, ?, ?, ?, ?)",
        ["18314844", "18314843", "10.5281/zenodo.18314844", "Test",
         json.dumps(raw_payload)],
    )
    # The other tables required for the link-table migration to no-op.
    conn.execute(
        "CREATE TABLE communities (community_id TEXT PRIMARY KEY, title TEXT, "
        "raw JSON, ingested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)",
    )
    conn.execute(
        "CREATE TABLE record_creators (record_id TEXT, creator_key TEXT, "
        "position INTEGER, PRIMARY KEY (record_id, creator_key))",
    )
    conn.execute(
        "CREATE TABLE record_communities (record_id TEXT, community_id TEXT, "
        "PRIMARY KEY (record_id, community_id))",
    )
    conn.execute(
        "CREATE TABLE files (record_id TEXT, file_key TEXT, file_id TEXT, "
        "size_bytes BIGINT, checksum TEXT, download_url TEXT, "
        "PRIMARY KEY (record_id, file_key))",
    )
    conn.execute(
        "CREATE TABLE creators (creator_key TEXT PRIMARY KEY, "
        "display_name TEXT, orcid TEXT, affiliation TEXT, raw JSON, "
        "ingested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)",
    )
    conn.execute(
        "CREATE TABLE chunks (chunk_id TEXT PRIMARY KEY, entity_type TEXT, "
        "entity_id TEXT, chunk_index INTEGER, text TEXT, token_count INTEGER, "
        "vector_id TEXT, embedded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)",
    )
    conn.close()

    store = ZenodoStore(db_path)
    store.bootstrap()
    conn = store.connect()

    new_cols = [r[1] for r in conn.execute("PRAGMA table_info('records')").fetchall()]
    for expected in (
        "concept_doi", "version", "revision", "created_at", "updated_at",
        "views", "unique_views", "downloads", "unique_downloads",
        "version_views", "version_unique_views",
        "version_downloads", "version_unique_downloads",
    ):
        assert expected in new_cols, f"new column {expected!r} missing"

    row = conn.execute(
        "SELECT concept_doi, version, revision, "
        "       views, unique_views, downloads, unique_downloads, "
        "       version_views, version_unique_views, "
        "       version_downloads, version_unique_downloads, "
        "       created_at, updated_at "
        "FROM records WHERE zenodo_id = 'https://zenodo.org/records/18314844'",
    ).fetchone()
    (concept_doi, version, revision,
     views, unique_views, downloads, unique_downloads,
     ver_views, ver_unique_views, ver_downloads, ver_unique_downloads,
     created_at, updated_at) = row
    # `concept_doi` is promoted to the canonical URL form by the
    # DOI migration that runs as part of bootstrap.
    assert concept_doi == "https://doi.org/10.5281/zenodo.18314843"
    assert version == "v2.1"
    assert revision == 5
    assert views == 4242
    assert unique_views == 3000
    assert downloads == 128
    assert unique_downloads == 100
    assert ver_views == 1000
    assert ver_unique_views == 750
    assert ver_downloads == 30
    assert ver_unique_downloads == 28
    assert created_at.isoformat().startswith("2024-01-15T10:30")
    assert updated_at.isoformat().startswith("2026-02-20T09:00")
    store.close()


def test_existing_record_ids_handles_iri_form(tmp_path: Path):
    """`existing_record_ids([bare])` should still return bare for downstream diffing."""
    db_path = tmp_path / "zenodo.duckdb"
    _seed_legacy_db(db_path)

    store = ZenodoStore(db_path)
    store.bootstrap()  # migrates to IRI form

    # Caller passes bare numeric ids (discovery sources extract those).
    found = store.existing_record_ids(["18314844", "99999999", "6594482"])
    store.close()

    # The two seeded records are present; the bogus one isn't.
    # 6594482 is a concept_recid of "19845281", so it should also match.
    assert "18314844" in found
    assert "6594482" in found
    assert "99999999" not in found
