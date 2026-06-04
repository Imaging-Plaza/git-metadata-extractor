"""Tests for OrcidStore lifecycle + upsert semantics."""

from __future__ import annotations


def test_bootstrap_creates_tables(tmp_store) -> None:
    expected = {"persons", "employments", "educations", "seeds", "chunks"}
    cur = tmp_store.connect().execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'main'",
    )
    actual = {row[0] for row in cur.fetchall()}
    assert expected.issubset(actual)


def test_upsert_seed_promotes_to_both(tmp_store) -> None:
    tmp_store.upsert_seed(orcid_id="0000-0002-1825-0097", discovered_via="openalex")
    tmp_store.upsert_seed(orcid_id="0000-0002-1825-0097", discovered_via="orcid_search")
    cur = tmp_store.connect().execute(
        "SELECT discovered_via FROM seeds WHERE orcid_id = ?",
        # v3.0.0: the store persists the canonical ORCID URL as the id.
        ["https://orcid.org/0000-0002-1825-0097"],
    )
    assert cur.fetchone()[0] == "both"


def test_upsert_person_then_replace_affiliations(tmp_store) -> None:
    person_row = {
        "orcid_id": "0000-0002-1825-0097",
        "given_name": "Alice",
        "family_name": "Example",
        "display_name": "Alice Example",
        "biography": None,
        "in_scope": True,
        "scope_reason": "test",
        "discovered_via": "manual",
    }
    tmp_store.upsert_person(person_row, raw={"name": "Alice Example"})
    affiliations = [
        {
            "orcid_id": "0000-0002-1825-0097",
            "seq": 0,
            "organization": "EPFL",
            "org_ror": None,
            "department": None,
            "role": "Researcher",
            "start_date": "2020-01-01",
            "end_date": None,
        },
        {
            "orcid_id": "0000-0002-1825-0097",
            "seq": 1,
            "organization": "ETHZ",
            "org_ror": None,
            "department": None,
            "role": "Postdoc",
            "start_date": "2018-01-01",
            "end_date": "2019-12-31",
        },
    ]
    tmp_store.replace_affiliations("employments", "0000-0002-1825-0097", affiliations)
    rows = tmp_store.list_employments("0000-0002-1825-0097")
    assert len(rows) == 2
    assert {r["organization"] for r in rows} == {"EPFL", "ETHZ"}

    # Replace-all: a fresh write with one row should drop the second.
    tmp_store.replace_affiliations(
        "employments",
        "0000-0002-1825-0097",
        affiliations[:1],
    )
    rows = tmp_store.list_employments("0000-0002-1825-0097")
    assert len(rows) == 1
    assert rows[0]["organization"] == "EPFL"


def test_stream_rows_for_embedding_skips_already_chunked(tmp_store) -> None:
    person_row = {
        "orcid_id": "0000-0002-1825-0097",
        "given_name": "Alice",
        "family_name": "Example",
        "display_name": "Alice Example",
        "biography": None,
        "in_scope": True,
        "scope_reason": "test",
        "discovered_via": "manual",
    }
    tmp_store.upsert_person(person_row, raw={"name": "Alice Example"})
    pending = list(tmp_store.stream_rows_for_embedding("persons"))
    assert len(pending) == 1

    tmp_store.upsert_chunk(
        chunk_id="x",
        entity_type="persons",
        # the person is stored under the canonical URL id; the chunk's
        # entity_id must match it (as the embed pipeline produces).
        entity_id="https://orcid.org/0000-0002-1825-0097",
        chunk_index=0,
        text="Alice Example",
        token_count=2,
        vector_id="x",
    )
    pending_after = list(tmp_store.stream_rows_for_embedding("persons"))
    assert pending_after == []


def test_in_scope_filter_excludes_out_of_scope(tmp_store) -> None:
    tmp_store.upsert_person(
        {
            "orcid_id": "0000-0001-3456-7898",
            "given_name": None,
            "family_name": None,
            "display_name": "Out of Scope",
            "biography": None,
            "in_scope": False,
            "scope_reason": None,
            "discovered_via": "manual",
        },
        raw={},
    )
    pending = list(tmp_store.stream_rows_for_embedding("persons"))
    assert pending == []
