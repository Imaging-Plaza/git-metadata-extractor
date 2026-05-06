"""Tests for `src/index/ror/storage/duckdb_store.py` (D16, PR 1).

Covers bootstrap, full-dump upsert + lookup parity with the in-memory
`DumpIndex`, scope-records bridge to Qdrant, and the manifest single-row
upsert. No live Qdrant or RCP — pure DuckDB on a tmp_path file.
"""

from __future__ import annotations

import pytest

from src.index.ror.storage.duckdb_store import (
    DuckDBStore,
    ScopeRecord,
    StoreManifest,
    build_search_blob,
    extract_record_columns,
    fold_for_search,
    vector_id_for,
)


@pytest.fixture
def isolated_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("INDEX_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def store(isolated_data_dir):
    s = DuckDBStore.open()
    yield s
    s.close()


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_fold_for_search_strips_accents_and_lowercases():
    assert fold_for_search("École") == "ecole"
    assert fold_for_search("Universität Bern") == "universitat bern"
    assert fold_for_search("EPFL") == "epfl"


def test_build_search_blob_concatenates_all_name_variants():
    record = {
        "names": [
            {"value": "École polytechnique fédérale de Lausanne", "types": ["ror_display", "label"]},
            {"value": "EPFL", "types": ["acronym"]},
            {"value": "ETH Lausanne", "types": ["alias"]},
        ],
    }
    blob = build_search_blob(record)
    assert "ecole polytechnique federale de lausanne" in blob
    assert "epfl" in blob
    assert "eth lausanne" in blob


def test_vector_id_is_deterministic_uuid5_over_url():
    rid = "https://ror.org/02s376052"
    a = vector_id_for(rid)
    b = vector_id_for(rid)
    assert a == b
    # Same shape as the existing Qdrant point id (UUIDv5 hex with dashes).
    assert len(a) == 36 and a.count("-") == 4


def test_extract_record_columns_handles_real_record_shape():
    record = {
        "id": "https://ror.org/02s376052",
        "names": [
            {"value": "École polytechnique fédérale de Lausanne", "types": ["ror_display", "label"]},
            {"value": "EPFL", "types": ["acronym"]},
        ],
        "status": "active",
        "established": 1853,
        "types": ["education", "facility"],
        "domains": ["epfl.ch"],
        "links": [{"type": "website", "value": "https://www.epfl.ch"}],
        "locations": [
            {"geonames_details": {
                "country_code": "CH",
                "country_name": "Switzerland",
                "name": "Lausanne",
                "country_subdivision_name": "Vaud",
            }},
        ],
        "external_ids": [{"type": "wikidata", "preferred": "Q262760"}],
        "relationships": [],
    }
    cols = extract_record_columns(record, ror_release_version="v2.6")
    assert cols["ror_id"] == "https://ror.org/02s376052"
    assert cols["ror_id_short"] == "02s376052"
    assert cols["name"] == "École polytechnique fédérale de Lausanne"
    assert cols["country_code"] == "CH"
    assert cols["country_name"] == "Switzerland"
    assert cols["city"] == "Lausanne"
    assert cols["region"] == "Vaud"
    assert cols["established"] == 1853
    assert cols["website"] == "https://www.epfl.ch"
    assert cols["status"] == "active"
    assert cols["types_json"] == ["education", "facility"]
    assert cols["acronyms_json"] == ["EPFL"]
    assert cols["ror_release_version"] == "v2.6"
    assert "ecole polytechnique federale de lausanne" in cols["search_blob"]
    assert "epfl" in cols["search_blob"]


def test_extract_record_columns_rejects_record_without_id():
    with pytest.raises(ValueError, match="missing a string `id`"):
        extract_record_columns({"names": [{"value": "X"}]})


# ---------------------------------------------------------------------------
# Store: bootstrap + records
# ---------------------------------------------------------------------------


def test_bootstrap_is_idempotent(isolated_data_dir):
    s1 = DuckDBStore.open()
    s1.bootstrap()
    s1.bootstrap()
    s1.close()
    # Re-open a second store on the same file.
    s2 = DuckDBStore.open()
    s2.bootstrap()
    assert s2.count_records() == 0
    s2.close()


def test_upsert_record_round_trip(store):
    record = {
        "id": "https://ror.org/02s376052",
        "names": [{"value": "EPFL", "types": ["ror_display"]}],
        "status": "active",
        "locations": [{"geonames_details": {"country_code": "CH"}}],
    }
    store.upsert_record(extract_record_columns(record))
    assert store.count_records() == 1
    hit = store.fetch_record("02s376052")
    assert hit is not None
    assert hit["ror_id"] == "https://ror.org/02s376052"
    assert hit["country_code"] == "CH"
    # JSON columns hydrate back to Python.
    assert hit["record"]["id"] == "https://ror.org/02s376052"


def test_upsert_record_overrides_existing_row(store):
    rid = "https://ror.org/02s376052"
    base = {"id": rid, "names": [{"value": "Old", "types": ["ror_display"]}], "status": "active"}
    store.upsert_record(extract_record_columns(base))
    updated = {"id": rid, "names": [{"value": "New", "types": ["ror_display"]}], "status": "inactive"}
    store.upsert_record(extract_record_columns(updated))
    assert store.count_records() == 1
    hit = store.fetch_record(rid)
    assert hit["name"] == "New"
    assert hit["status"] == "inactive"


def test_bulk_replace_records_via_copy_from_csv(store):
    rows = [
        extract_record_columns({
            "id": f"https://ror.org/test{i:04d}",
            "names": [{"value": f"Org {i}", "types": ["ror_display"]}],
            "status": "active",
            "locations": [{"geonames_details": {"country_code": "CH"}}],
        })
        for i in range(50)
    ]
    n = store.bulk_replace_records(rows, csv_chunk_size=20)
    assert n == 50
    assert store.count_records() == 50
    hit = store.fetch_record("test0007")
    assert hit is not None
    assert hit["country_code"] == "CH"


def test_bulk_replace_records_truncates_previous_state(store):
    first = [extract_record_columns({
        "id": f"https://ror.org/old{i}", "names": [{"value": f"Old {i}", "types": ["ror_display"]}],
    }) for i in range(5)]
    store.bulk_replace_records(first)
    assert store.count_records() == 5

    second = [extract_record_columns({
        "id": f"https://ror.org/new{i}", "names": [{"value": f"New {i}", "types": ["ror_display"]}],
    }) for i in range(3)]
    store.bulk_replace_records(second)
    assert store.count_records() == 3
    # First-batch records are gone.
    assert store.fetch_record("old0") is None
    assert store.fetch_record("new0") is not None


def test_bulk_replace_records_preserves_json_columns(store):
    record = {
        "id": "https://ror.org/02s376052",
        "names": [
            {"value": "École polytechnique fédérale de Lausanne", "types": ["ror_display", "label"]},
            {"value": "EPFL", "types": ["acronym"]},
        ],
        "status": "active",
        "types": ["education", "facility"],
        "external_ids": [{"type": "wikidata", "preferred": "Q262760"}],
        "locations": [{"geonames_details": {"country_code": "CH"}}],
    }
    store.bulk_replace_records([extract_record_columns(record)])
    hit = store.fetch_record("02s376052")
    assert hit is not None
    # JSON columns parsed back to Python.
    assert hit["types_json"] == ["education", "facility"]
    assert hit["acronyms_json"] == ["EPFL"]
    assert hit["external_ids_json"][0]["preferred"] == "Q262760"
    # `record` round-trip preserves the full original.
    assert hit["record"]["id"] == "https://ror.org/02s376052"
    assert hit["record"]["names"][0]["value"].startswith("École")


def test_bulk_upsert_under_transaction(store):
    rows = [
        extract_record_columns({
            "id": f"https://ror.org/test{i:04d}",
            "names": [{"value": f"Org {i}", "types": ["ror_display"]}],
            "status": "active",
            "locations": [{"geonames_details": {"country_code": "CH" if i % 2 == 0 else "FR"}}],
        })
        for i in range(20)
    ]
    with store.transaction():
        n = store.upsert_records(rows)
    assert n == 20
    assert store.count_records() == 20


# ---------------------------------------------------------------------------
# Store: lookup parity with DumpIndex
# ---------------------------------------------------------------------------


@pytest.fixture
def populated_store(store):
    records = [
        {
            "id": "https://ror.org/02s376052",
            "names": [
                {"value": "École polytechnique fédérale de Lausanne", "types": ["ror_display", "label"]},
                {"value": "EPFL", "types": ["acronym"]},
            ],
            "status": "active",
            "types": ["education"],
            "locations": [{"geonames_details": {"country_code": "CH", "name": "Lausanne"}}],
        },
        {
            "id": "https://ror.org/05a28rw58",
            "names": [
                {"value": "ETH Zurich", "types": ["ror_display", "label"]},
                {"value": "Eidgenössische Technische Hochschule Zürich", "types": ["alias"]},
            ],
            "status": "active",
            "types": ["education"],
            "locations": [{"geonames_details": {"country_code": "CH", "name": "Zürich"}}],
        },
        {
            "id": "https://ror.org/02nv7yv05",
            "names": [{"value": "Universität Bern", "types": ["ror_display", "label"]}],
            "status": "active",
            "types": ["education"],
            "locations": [{"geonames_details": {"country_code": "CH", "name": "Bern"}}],
        },
        {
            "id": "https://ror.org/abandoned1",
            "names": [{"value": "Some Old Lab", "types": ["ror_display"]}],
            "status": "withdrawn",
            "types": ["facility"],
            "locations": [{"geonames_details": {"country_code": "FR", "name": "Paris"}}],
        },
    ]
    with store.transaction():
        store.upsert_records(extract_record_columns(r) for r in records)
    return store


def test_lookup_by_exact_ror_id_url_or_bare(populated_store):
    by_url = populated_store.lookup(ror_id="https://ror.org/02s376052")
    by_bare = populated_store.lookup(ror_id="02s376052")
    assert len(by_url) == len(by_bare) == 1
    assert by_url[0]["ror_id"] == "https://ror.org/02s376052"
    assert by_bare[0]["ror_id"] == "https://ror.org/02s376052"


def test_lookup_by_text_uses_search_blob_with_accent_folding(populated_store):
    # `Universitat` (no umlaut) should still hit "Universität Bern".
    hits = populated_store.lookup(text="Universitat Bern")
    names = [h["name"] for h in hits]
    assert "Universität Bern" in names

    # `Ecole` (no acute) should still hit EPFL via its display name.
    hits = populated_store.lookup(text="Ecole Lausanne")
    names = [h["name"] for h in hits]
    assert "École polytechnique fédérale de Lausanne" in names


def test_lookup_text_ranks_by_token_match_count(populated_store):
    hits = populated_store.lookup(text="Eidgenössische Hochschule Zürich")
    # ETH Zurich's alias contains all three tokens (after folding); should rank #1.
    assert hits[0]["ror_id"] == "https://ror.org/05a28rw58"


def test_lookup_country_filter(populated_store):
    fr_only = populated_store.lookup(country="FR")
    assert {h["ror_id"] for h in fr_only} == {"https://ror.org/abandoned1"}


def test_lookup_status_filter(populated_store):
    active_only = populated_store.lookup(status="active", limit=10)
    assert all(h["status"] == "active" for h in active_only)
    assert len(active_only) == 3


def test_lookup_type_filter(populated_store):
    education = populated_store.lookup(type_="education", limit=10)
    assert {h["ror_id"] for h in education} == {
        "https://ror.org/02s376052",
        "https://ror.org/05a28rw58",
        "https://ror.org/02nv7yv05",
    }


def test_lookup_combined_filters(populated_store):
    hits = populated_store.lookup(text="Bern", country="CH", status="active", limit=5)
    assert [h["name"] for h in hits] == ["Universität Bern"]


def test_lookup_text_with_no_matching_tokens_returns_empty(populated_store):
    assert populated_store.lookup(text="!!!") == []


def test_lookup_limit_is_respected(populated_store):
    hits = populated_store.lookup(country="CH", limit=2)
    assert len(hits) == 2


# ---------------------------------------------------------------------------
# Store: scope_records (Qdrant bridge)
# ---------------------------------------------------------------------------


def test_set_scope_records_replaces_atomically(store):
    rid = "https://ror.org/02s376052"
    store.upsert_record(extract_record_columns({
        "id": rid,
        "names": [{"value": "EPFL", "types": ["ror_display"]}],
    }))

    rows_v1 = [
        ScopeRecord(
            scope_mode="epfl_ethz",
            ror_id=rid,
            text="Name: EPFL",
            vector_id=vector_id_for(rid),
        ),
    ]
    n1 = store.set_scope_records("epfl_ethz", rows_v1)
    assert n1 == 1
    assert store.count_scope_records("epfl_ethz") == 1

    # Rebuilding the scope swaps the row out — no leftover from v1.
    rows_v2 = [
        ScopeRecord(
            scope_mode="epfl_ethz",
            ror_id=rid,
            text="Name: École polytechnique fédérale de Lausanne",
            vector_id=vector_id_for(rid),
        ),
    ]
    n2 = store.set_scope_records("epfl_ethz", rows_v2)
    assert n2 == 1
    assert store.count_scope_records("epfl_ethz") == 1
    cur = store.connect().execute(
        "SELECT text FROM scope_records WHERE scope_mode = ? AND ror_id = ?",
        ["epfl_ethz", rid],
    )
    assert cur.fetchone()[0].startswith("Name: École")


def test_scope_records_isolate_by_scope_mode(store):
    rid = "https://ror.org/02s376052"
    store.upsert_record(extract_record_columns({
        "id": rid, "names": [{"value": "EPFL", "types": ["ror_display"]}],
    }))
    sr = ScopeRecord(scope_mode="x", ror_id=rid, text="t", vector_id=vector_id_for(rid))
    store.set_scope_records("epfl_ethz", [sr.model_copy(update={"scope_mode": "epfl_ethz"})])
    store.set_scope_records("worldwide", [sr.model_copy(update={"scope_mode": "worldwide"})])
    assert store.count_scope_records("epfl_ethz") == 1
    assert store.count_scope_records("worldwide") == 1
    # Replacing one scope leaves the other intact.
    store.set_scope_records("epfl_ethz", [])
    assert store.count_scope_records("epfl_ethz") == 0
    assert store.count_scope_records("worldwide") == 1


# ---------------------------------------------------------------------------
# Store: manifests
# ---------------------------------------------------------------------------


def test_set_manifest_round_trip(store):
    m = StoreManifest(
        scope_mode="switzerland",
        record_count=1854,
        embedding_model="Qwen/Qwen3-Embedding-8B",
        embedding_dim=4096,
        reranker_model="Qwen/Qwen3-Reranker-8B",
        ror_release_version="v2.6",
        ror_release_doi="10.5281/zenodo.19576723",
    )
    store.set_manifest(m)
    fetched = store.fetch_manifest("switzerland")
    assert fetched is not None
    assert fetched["record_count"] == 1854
    assert fetched["embedding_dim"] == 4096
    assert fetched["ror_release_version"] == "v2.6"


def test_set_manifest_overrides_on_conflict(store):
    base = StoreManifest(
        scope_mode="switzerland",
        record_count=1000,
        embedding_model="Qwen/Qwen3-Embedding-8B",
        embedding_dim=4096,
        reranker_model="Qwen/Qwen3-Reranker-8B",
    )
    store.set_manifest(base)
    updated = base.model_copy(update={"record_count": 1854, "ror_release_version": "v2.7"})
    store.set_manifest(updated)
    fetched = store.fetch_manifest("switzerland")
    assert fetched["record_count"] == 1854
    assert fetched["ror_release_version"] == "v2.7"
