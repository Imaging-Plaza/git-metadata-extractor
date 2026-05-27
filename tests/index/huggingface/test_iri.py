"""Tests for HuggingFace IRI helpers + the bootstrap CTAS migration."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from src.index.huggingface.iri import (
    dataset_iri,
    iri_for_entity_type,
    model_iri,
    namespace_iri,
    parse_namespace_slug,
    parse_repo_id,
    space_iri,
)
from src.index.huggingface.storage.duckdb_store import DuckDBStore


# ---------------------------------------------------------------------------
# Pure helpers — URL shapes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fn,bare,expected",
    [
        (namespace_iri, "epfl-llm", "https://huggingface.co/epfl-llm"),
        (model_iri, "epfl-llm/meditron-7b",
         "https://huggingface.co/epfl-llm/meditron-7b"),
        (dataset_iri, "epfl-llm/guidelines",
         "https://huggingface.co/datasets/epfl-llm/guidelines"),
        (space_iri, "EPFL-VILAB/4M",
         "https://huggingface.co/spaces/EPFL-VILAB/4M"),
    ],
)
def test_iri_helpers_promote_bare_ids(fn, bare, expected):
    assert fn(bare) == expected


def test_iri_helpers_are_idempotent():
    """Passing an already-IRI input should round-trip unchanged (modulo
    trailing-slash stripping)."""
    iri = "https://huggingface.co/datasets/epfl-llm/guidelines"
    assert dataset_iri(iri) == iri
    assert dataset_iri(iri + "/") == iri


def test_iri_helpers_strip_www_host():
    assert namespace_iri("https://www.huggingface.co/epfl-llm") == (
        "https://huggingface.co/epfl-llm"
    )


def test_dataset_iri_does_not_double_prefix():
    """Caller-passed `datasets/<author>/<name>` is recognised."""
    assert dataset_iri("datasets/epfl-llm/guidelines") == (
        "https://huggingface.co/datasets/epfl-llm/guidelines"
    )


def test_iri_for_entity_type_dispatches():
    bare = "epfl-llm/meditron-7b"
    assert iri_for_entity_type("model", bare) == model_iri(bare)
    assert iri_for_entity_type("dataset", bare) == dataset_iri(bare)
    assert iri_for_entity_type("space", bare) == space_iri(bare)
    assert iri_for_entity_type("org", "epfl-llm") == namespace_iri("epfl-llm")
    # Unknown discriminator passes through.
    assert iri_for_entity_type("garbage", "x") == "x"


def test_parse_inverses():
    assert parse_namespace_slug("https://huggingface.co/epfl-llm") == "epfl-llm"
    assert parse_namespace_slug("epfl-llm") == "epfl-llm"
    assert parse_namespace_slug(
        "https://huggingface.co/datasets/epfl-llm/guidelines",
    ) == "epfl-llm"  # first path segment after prefix strip
    assert parse_repo_id("https://huggingface.co/epfl-llm/meditron-7b") == (
        "epfl-llm/meditron-7b"
    )
    assert parse_repo_id(
        "https://huggingface.co/datasets/epfl-llm/guidelines",
    ) == "epfl-llm/guidelines"
    assert parse_repo_id(
        "https://huggingface.co/spaces/EPFL-VILAB/4M",
    ) == "EPFL-VILAB/4M"


# ---------------------------------------------------------------------------
# Bootstrap CTAS migration
# ---------------------------------------------------------------------------


def _seed_legacy_hf_db(db_path: Path) -> None:
    """Build a pre-PR-shape DB seeded with bare ids across every PK and FK."""
    schema = (
        Path(__file__).resolve().parents[3]
        / "src" / "index" / "huggingface" / "storage" / "schema.sql"
    ).read_text(encoding="utf-8")
    conn = duckdb.connect(str(db_path))
    conn.execute(schema)
    conn.execute(
        "INSERT INTO orgs (slug, namespace_kind, scope) VALUES "
        "('epfl-llm', 'org', 'epfl'), "
        "('caviri', 'user', 'epfl'), "
        "('EPFL-VILAB', 'org', 'epfl')",
    )
    conn.execute(
        "INSERT INTO models (repo_id, author) VALUES "
        "('epfl-llm/meditron-7b', 'epfl-llm'), "
        "('EPFL-VILAB/4M-7-T2I_L_CC12M', 'EPFL-VILAB')",
    )
    conn.execute(
        "INSERT INTO datasets (repo_id, author) VALUES "
        "('epfl-llm/guidelines', 'epfl-llm')",
    )
    conn.execute(
        "INSERT INTO spaces (repo_id, author) VALUES "
        "('EPFL-VILAB/4M', 'EPFL-VILAB')",
    )
    conn.execute(
        "INSERT INTO chunks (chunk_id, entity_type, repo_id, chunk_index, text, "
        "token_count, vector_id) VALUES "
        "('c1', 'model', 'epfl-llm/meditron-7b', 0, 't', 1, 'v1'), "
        "('c2', 'dataset', 'epfl-llm/guidelines', 0, 't', 1, 'v2'), "
        "('c3', 'space', 'EPFL-VILAB/4M', 0, 't', 1, 'v3'), "
        "('c4', 'org', 'epfl-llm', 0, 't', 1, 'v4')",
    )
    conn.close()


def test_bootstrap_migrates_every_pk_and_fk(tmp_path: Path):
    db_path = tmp_path / "hf.duckdb"
    _seed_legacy_hf_db(db_path)

    store = DuckDBStore(db_path)
    store.bootstrap()
    conn = store.connect()

    # No table has any bare id left.
    for table, column in [
        ("orgs", "slug"),
        ("models", "repo_id"),
        ("models", "author"),
        ("datasets", "repo_id"),
        ("datasets", "author"),
        ("spaces", "repo_id"),
        ("spaces", "author"),
        ("chunks", "repo_id"),
    ]:
        bare = conn.execute(
            f"SELECT COUNT(*) FROM {table} "
            f"WHERE {column} IS NOT NULL AND {column} NOT LIKE 'https://%'",
        ).fetchone()[0]
        assert bare == 0, f"{table}.{column}: {bare} bare-id row(s) remain"

    # Each catalog got the right URL shape.
    assert conn.execute(
        "SELECT slug FROM orgs WHERE namespace_kind = 'user'",
    ).fetchone()[0] == "https://huggingface.co/caviri"

    model_id = conn.execute(
        "SELECT repo_id FROM models WHERE repo_id LIKE '%meditron-7b'",
    ).fetchone()[0]
    assert model_id == "https://huggingface.co/epfl-llm/meditron-7b"

    dataset_id = conn.execute("SELECT repo_id FROM datasets").fetchone()[0]
    assert dataset_id == "https://huggingface.co/datasets/epfl-llm/guidelines"

    space_id = conn.execute("SELECT repo_id FROM spaces").fetchone()[0]
    assert space_id == "https://huggingface.co/spaces/EPFL-VILAB/4M"

    # Chunks: each entity_type got its own URL shape.
    chunks = dict(conn.execute(
        "SELECT entity_type, repo_id FROM chunks ORDER BY entity_type",
    ).fetchall())
    assert chunks["model"].startswith("https://huggingface.co/epfl-llm/")
    assert chunks["dataset"].startswith("https://huggingface.co/datasets/")
    assert chunks["space"].startswith("https://huggingface.co/spaces/")
    assert chunks["org"] == "https://huggingface.co/epfl-llm"

    store.close()


def test_bootstrap_is_idempotent_after_migration(tmp_path: Path):
    db_path = tmp_path / "hf.duckdb"
    _seed_legacy_hf_db(db_path)

    store = DuckDBStore(db_path)
    store.bootstrap()
    store.close()

    for _ in range(3):
        store = DuckDBStore(db_path)
        store.bootstrap()
        store.close()

    conn = duckdb.connect(str(db_path), read_only=True)
    # No bare ids snuck back, and row counts are preserved end-to-end.
    bare_models = conn.execute(
        "SELECT COUNT(*) FROM models WHERE repo_id NOT LIKE 'https://%'",
    ).fetchone()[0]
    assert bare_models == 0
    assert conn.execute("SELECT COUNT(*) FROM models").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 4
    conn.close()


def test_lookup_methods_accept_bare_or_iri_input(tmp_path: Path):
    """`fetch_org` / `fetch_repo` / `repo_sha` should accept both shapes."""
    db_path = tmp_path / "hf.duckdb"
    _seed_legacy_hf_db(db_path)
    store = DuckDBStore(db_path)
    store.bootstrap()

    # Bare input — promoted internally.
    org_row = store.fetch_org("epfl-llm")
    assert org_row is not None
    assert org_row["slug"] == "https://huggingface.co/epfl-llm"

    # IRI input — passes through.
    org_iri = store.fetch_org("https://huggingface.co/epfl-llm")
    assert org_iri == org_row

    # repo_sha + fetch_repo accept bare.
    assert store.fetch_repo("models", "epfl-llm/meditron-7b") is not None
    assert store.fetch_repo("datasets", "epfl-llm/guidelines") is not None
    store.close()
