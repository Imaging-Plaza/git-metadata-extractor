"""URL regex extraction + canonicalization via the v2 classifier."""

from __future__ import annotations

import pytest

from src.index.openalex.ingest.github_extract import (
    extract_and_persist_for_work,
    extract_github_urls,
)


@pytest.mark.openalex()
def test_extracts_basic_url():
    urls = extract_github_urls(
        "Code at https://github.com/sdsc-ordes/gimie is available."
    )
    assert urls == ["https://github.com/sdsc-ordes/gimie"]


@pytest.mark.openalex()
def test_strips_trailing_punctuation():
    urls = extract_github_urls(
        "See https://github.com/owner/repo. And also (https://github.com/owner/two)."
    )
    assert "https://github.com/owner/repo" in urls
    assert "https://github.com/owner/two" in urls


@pytest.mark.openalex()
def test_handles_www_subdomain():
    urls = extract_github_urls("Visit https://www.github.com/owner/repo for code.")
    assert urls == ["https://www.github.com/owner/repo"]


@pytest.mark.openalex()
def test_persists_canonicalized_url(tmp_store):
    text = (
        "Source code is available at https://github.com/sdsc-ordes/gimie.git "
        "and the issue tracker at "
        "https://github.com/sdsc-ordes/gimie/issues/42."
    )
    persisted = extract_and_persist_for_work(
        tmp_store,
        work_id="https://openalex.org/W1",
        text=text,
        source="abstract",
    )
    # The same canonical repo URL is dedup'd across both .git suffix and
    # /issues/ subresource paths.
    assert persisted == 1
    rows = tmp_store.connect().execute(
        "SELECT normalized_url, owner, repo, source FROM work_github_urls",
    ).fetchall()
    assert len(rows) == 1
    norm, owner, repo, source = rows[0]
    assert norm == "https://github.com/sdsc-ordes/gimie"
    assert owner == "sdsc-ordes"
    assert repo == "gimie"
    assert source == "abstract"


@pytest.mark.openalex()
def test_invalid_source_raises(tmp_store):
    with pytest.raises(ValueError, match="Invalid source"):
        extract_and_persist_for_work(
            tmp_store,
            work_id="W1",
            text="x",
            source="bogus",
        )
