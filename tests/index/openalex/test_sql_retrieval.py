"""Read-only SQL surface: predefined queries + ad-hoc guard."""

from __future__ import annotations

import pytest

from src.index.openalex.retrieval.sql import run_adhoc, run_predefined


@pytest.mark.openalex()
def test_adhoc_select_passes(tmp_store):
    tmp_store.upsert_work(
        {
            "openalex_id": "W1",
            "doi": None,
            "title": "X",
            "abstract": None,
            "publication_year": 2024,
            "primary_topic_id": None,
            "primary_source_id": None,
        },
        raw={"id": "W1"},
    )
    rows = run_adhoc("SELECT openalex_id FROM works", store=tmp_store)
    assert rows == [{"openalex_id": "W1"}]


@pytest.mark.openalex()
def test_adhoc_with_cte_passes(tmp_store):
    rows = run_adhoc(
        "WITH x AS (SELECT 1 AS n) SELECT n FROM x",
        store=tmp_store,
    )
    assert rows == [{"n": 1}]


@pytest.mark.openalex()
@pytest.mark.parametrize(
    "bad_sql",
    [
        "DROP TABLE works",
        "DELETE FROM works",
        "ATTACH 'foo.db' AS f",
        "PRAGMA database_list",
        "INSERT INTO works VALUES (1)",
        "UPDATE works SET title = 'x'",
        "  CREATE TABLE x (a INT)",
    ],
)
def test_adhoc_rejects_dangerous_statements(tmp_store, bad_sql: str):
    with pytest.raises(ValueError):
        run_adhoc(bad_sql, store=tmp_store)


@pytest.mark.openalex()
def test_predefined_count_by_entity(tmp_store):
    rows = run_predefined("count_by_entity", store=tmp_store)
    entities = {r["entity"] for r in rows}
    assert "works" in entities
    assert "work_github_urls" in entities


@pytest.mark.openalex()
def test_predefined_unknown(tmp_store):
    with pytest.raises(ValueError, match="Unknown predefined"):
        run_predefined("nope", store=tmp_store)


@pytest.mark.openalex()
def test_predefined_top_works_by_year(tmp_store):
    for i, year in enumerate([2024, 2024, 2023]):
        tmp_store.upsert_work(
            {
                "openalex_id": f"W{i}",
                "doi": None,
                "title": f"T{i}",
                "abstract": None,
                "publication_year": year,
                "primary_topic_id": None,
                "primary_source_id": None,
            },
            raw={"id": f"W{i}"},
        )
    rows = run_predefined(
        "top_works_by_year",
        params={"year": 2024, "limit": 10},
        store=tmp_store,
    )
    assert len(rows) == 2
    assert all(r["publication_year"] == 2024 for r in rows)
