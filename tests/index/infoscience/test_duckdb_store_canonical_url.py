"""v3.0.0: infoscience DuckDB ids (and junction FKs) are canonical URLs.

Covers all three write surfaces that must agree on the id form:
  * the per-row Python upserts on `InfoscienceStore`,
  * the bulk-SQL `ingest_raw` path,
  * the in-place bootstrap migration of legacy bare-UUID rows.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.index.infoscience.storage.duckdb_store import InfoscienceStore
from src.index.infoscience.storage.ingest_raw import ingest_articles

# Valid UUID4s (version nibble 4, variant 8/9/a/b).
_ART = "8f486100-04da-40e6-9c00-0411efbfd978"
_PERSON = "a7bc3696-16f4-49a8-a0d1-fabc1d9ad261"
_ORG = "372d8be7-b45f-47ce-8689-2f7872fd4c2f"

_PUB_URL = f"https://infoscience.epfl.ch/entities/publication/{_ART}"
_PERSON_URL = f"https://infoscience.epfl.ch/entities/person/{_PERSON}"
_ORG_URL = f"https://infoscience.epfl.ch/entities/orgunit/{_ORG}"


@pytest.fixture()
def store(tmp_path: Path) -> InfoscienceStore:
    s = InfoscienceStore.open(tmp_path / "infoscience.duckdb")
    yield s
    s.close()


def _one(store: InfoscienceStore, sql: str):
    return store.connect().execute(sql).fetchone()


def test_python_upserts_store_url_ids(store: InfoscienceStore) -> None:
    store.upsert_article({"article_uuid": _ART, "title": "T"}, raw={})
    store.upsert_person({"person_uuid": _PERSON, "primary_affiliation_uuid": _ORG}, raw={})
    store.upsert_organization({"org_uuid": _ORG, "parent_org_uuid": _ORG}, raw={})
    store.upsert_article_persons(_ART, [(_PERSON, 0)])
    store.upsert_article_orgs(_ART, [(_ORG, "cris.virtual.department")])
    store.upsert_article_links(_ART, [("github", "https://github.com/x/y", "body_text")])

    assert _one(store, "SELECT article_uuid, infoscience_url FROM articles")[0] == _PUB_URL
    assert _one(store, "SELECT article_uuid FROM articles")[0] == _PUB_URL
    assert _one(store, "SELECT infoscience_url FROM articles")[0] == _PUB_URL
    assert _one(store, "SELECT person_uuid, primary_affiliation_uuid FROM persons") == (
        _PERSON_URL, _ORG_URL,
    )
    assert _one(store, "SELECT org_uuid FROM organizations")[0] == _ORG_URL
    # Junction FKs are URL-ified on both sides.
    assert _one(store, "SELECT article_uuid, person_uuid FROM article_persons") == (
        _PUB_URL, _PERSON_URL,
    )
    assert _one(store, "SELECT article_uuid, org_uuid FROM article_orgs") == (_PUB_URL, _ORG_URL)
    assert _one(store, "SELECT article_uuid FROM article_links")[0] == _PUB_URL


def test_python_upserts_are_idempotent_on_url(store: InfoscienceStore) -> None:
    # Upserting an already-canonical id must not double-wrap.
    store.upsert_article({"article_uuid": _PUB_URL, "title": "T"}, raw={})
    assert _one(store, "SELECT count(*) FROM articles")[0] == 1
    assert _one(store, "SELECT article_uuid FROM articles")[0] == _PUB_URL


def test_bulk_ingest_raw_stores_url_ids(store: InfoscienceStore, tmp_path: Path) -> None:
    items_dir = tmp_path / "items"
    items_dir.mkdir()
    item = {
        "uuid": _ART,
        "metadata": {
            "dc.title": [{"value": "Bulk title"}],
            "dc.contributor.author": [
                {"value": "Alice", "authority": _PERSON, "place": "0"},
            ],
            "cris.virtual.department": [{"value": "Lab", "authority": _ORG}],
        },
    }
    (items_dir / f"{_ART}.json").write_text(json.dumps(item), encoding="utf-8")

    ingest_articles(store, items_dir=items_dir)

    assert _one(store, "SELECT article_uuid, infoscience_url FROM articles") == (_PUB_URL, _PUB_URL)
    assert _one(store, "SELECT article_uuid, person_uuid FROM article_persons") == (
        _PUB_URL, _PERSON_URL,
    )
    assert _one(store, "SELECT article_uuid, org_uuid FROM article_orgs") == (_PUB_URL, _ORG_URL)


def test_bootstrap_migrates_legacy_bare_uuid_rows(tmp_path: Path) -> None:
    db = tmp_path / "legacy.duckdb"
    store = InfoscienceStore.open(db)
    conn = store.connect()
    # Simulate a pre-v3 DB: insert rows with bare UUID ids, bypassing the
    # canonicalizing upserts.
    conn.execute(
        "INSERT INTO articles (article_uuid, title, infoscience_url) VALUES (?, ?, ?)",
        [_ART, "Legacy", f"https://infoscience.epfl.ch/entities/publication/{_ART}"],
    )
    conn.execute(
        "INSERT INTO article_persons (article_uuid, person_uuid, position) VALUES (?, ?, ?)",
        [_ART, _PERSON, 0],
    )
    store.close()

    # Re-open → bootstrap runs the migration.
    store2 = InfoscienceStore.open(db)
    assert _one(store2, "SELECT article_uuid FROM articles")[0] == _PUB_URL
    assert _one(store2, "SELECT article_uuid, person_uuid FROM article_persons") == (
        _PUB_URL, _PERSON_URL,
    )
    # Idempotent: a second bootstrap leaves the URLs untouched.
    store2.bootstrap()
    assert _one(store2, "SELECT article_uuid FROM articles")[0] == _PUB_URL
    store2.close()
