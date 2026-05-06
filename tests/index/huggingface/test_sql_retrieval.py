"""Read-only SQL surface: predefined + ad-hoc guards."""

from __future__ import annotations

import pytest

from src.index.huggingface.retrieval.sql import (
    PREDEFINED_QUERIES,
    run_adhoc,
    run_predefined,
)


def test_predefined_known_names():
    assert "count_by_entity" in PREDEFINED_QUERIES
    assert "top_models_by_downloads" in PREDEFINED_QUERIES


def test_predefined_unknown_raises(tmp_store):
    with pytest.raises(ValueError, match="Unknown predefined query"):
        run_predefined("not_a_real_query", store=tmp_store)


def test_adhoc_select_allowed(tmp_store):
    rows = run_adhoc("SELECT 1 AS n", store=tmp_store)
    assert rows == [{"n": 1}]


def test_adhoc_rejects_non_select():
    with pytest.raises(ValueError, match="Only SELECT/WITH"):
        run_adhoc("DELETE FROM models")


def test_adhoc_rejects_forbidden_keyword():
    with pytest.raises(ValueError, match="Forbidden keyword"):
        run_adhoc("SELECT * FROM models; DROP TABLE chunks")


def test_count_by_entity_predefined(tmp_store):
    tmp_store.upsert_model({"repo_id": "a/b"}, raw={})
    rows = run_predefined("count_by_entity", store=tmp_store)
    by = {r["entity"]: r["n"] for r in rows}
    assert by["models"] == 1
    assert by["datasets"] == 0
    assert by["chunks"] == 0
