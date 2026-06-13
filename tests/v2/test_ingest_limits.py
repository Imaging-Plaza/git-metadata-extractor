# tests/v2/test_ingest_limits.py
"""Audit findings ingest-list-no-maxlen / search-query-unbounded-string:
bound the per-request ingest batch size and the search query length.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.v2.api_models.contracts import (
    GitHubIngestRequest,
    IndexSearchRequest,
    OamonitorIngestRequest,
    ZenodoIngestRequest,
)


def test_ingest_batch_at_cap_ok():
    ZenodoIngestRequest(ids=["1"] * 1000)
    GitHubIngestRequest(repos=["o/r"] * 1000)
    OamonitorIngestRequest(items=[{"entity": "journals", "id": "x"}] * 1000)


def test_ingest_batch_over_cap_rejected():
    with pytest.raises(ValidationError):
        ZenodoIngestRequest(ids=["1"] * 1001)
    with pytest.raises(ValidationError):
        GitHubIngestRequest(repos=["o/r"] * 1001)
    with pytest.raises(ValidationError):
        OamonitorIngestRequest(items=[{"entity": "journals", "id": "x"}] * 1001)


def test_search_query_length_capped():
    IndexSearchRequest(query="a" * 4000)
    with pytest.raises(ValidationError):
        IndexSearchRequest(query="a" * 4001)
