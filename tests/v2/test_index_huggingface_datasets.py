"""Unit tests for the huggingface_datasets index module."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.index.huggingface_datasets.embed.pipeline import (
    _row_to_payload,
    _row_to_text,
)
from src.index.huggingface_datasets.ingest.datasets import (
    _extract_citation_dois,
    _paperswithcode_url,
    _record_from_info,
    ingest_single_dataset,
)
from src.index.huggingface_datasets.models import DatasetRecord
from src.index.huggingface_datasets.storage.duckdb_store import (
    HuggingFaceDatasetsStore,
)


class _FakeHFClient:
    def __init__(self, repo_id: str, info: Any | None) -> None:
        self._repo_id = repo_id
        self._info = info

    def dataset_info(self, repo_id: str, *, expand: tuple[str, ...]) -> Any | None:
        del expand
        if repo_id != self._repo_id:
            return None
        return self._info


@pytest.fixture()
def datasets_store(tmp_path: Path) -> HuggingFaceDatasetsStore:
    store = HuggingFaceDatasetsStore.open(tmp_path / "huggingface_datasets.duckdb")
    yield store
    store.close()


# ---------------------------------------------------------------------------
# Citation DOI extraction
# ---------------------------------------------------------------------------


def test_extract_citation_dois_from_bibtex() -> None:
    bibtex = """
    @article{smith2024,
      title = {A great dataset},
      doi = {10.1234/abcd.2024.5678},
      author = {Smith, Alice}
    }
    See also 10.5678/xyz.999 for a related dataset.
    """
    dois = _extract_citation_dois(bibtex)
    assert "https://doi.org/10.1234/abcd.2024.5678" in dois
    assert "https://doi.org/10.5678/xyz.999" in dois


def test_extract_citation_dois_dedupes() -> None:
    # Real DOIs need a 4+ digit registrant code; the regex enforces it.
    bibtex = "doi = {10.1234/x}\n10.1234/x again"
    dois = _extract_citation_dois(bibtex)
    assert dois == ["https://doi.org/10.1234/x"]


def test_extract_citation_dois_handles_no_doi() -> None:
    assert _extract_citation_dois("just some text") == []
    assert _extract_citation_dois(None) == []
    assert _extract_citation_dois("") == []


def test_paperswithcode_url_handles_missing() -> None:
    assert _paperswithcode_url(None) is None
    assert _paperswithcode_url("") is None
    assert _paperswithcode_url("imagenet") == "https://paperswithcode.com/dataset/imagenet"


# ---------------------------------------------------------------------------
# DatasetInfo → DatasetRecord
# ---------------------------------------------------------------------------


def test_record_from_info_populates_dataset_specific_fields() -> None:
    info = SimpleNamespace(
        author="username",
        sha="abc",
        downloads=42,
        likes=10,
        tags=["language:en", "size:1k"],
        card_data={"license": "cc-by-4.0", "pretty_name": "MyDataset"},
        citation="@misc{x, doi = {10.5281/zenodo.12345}}",
        paperswithcode_id="my-dataset",
    )
    record = _record_from_info("user/my-dataset", info)
    assert record.repo_id == "user/my-dataset"
    assert record.license == "cc-by-4.0"
    assert record.downloads == 42
    assert record.likes == 10
    assert record.citation_text.startswith("@misc{")
    assert record.paperswithcode_url == "https://paperswithcode.com/dataset/my-dataset"
    assert record.citation_dois == ["https://doi.org/10.5281/zenodo.12345"]


# ---------------------------------------------------------------------------
# DuckDB round-trip
# ---------------------------------------------------------------------------


def test_ingest_single_dataset_round_trips(
    datasets_store: HuggingFaceDatasetsStore,
) -> None:
    info = SimpleNamespace(
        author="alice",
        downloads=5,
        tags=[],
        card_data={"license": "mit"},
    )
    client = _FakeHFClient("alice/x", info)
    outcome = ingest_single_dataset(
        config=object(), store=datasets_store, client=client, repo_id="alice/x",
    )
    assert outcome == "ingested"
    row = datasets_store.fetch_dataset("alice/x")
    assert row is not None
    assert row["license"] == "mit"


def test_ingest_skips_unknown_repo(datasets_store: HuggingFaceDatasetsStore) -> None:
    client = _FakeHFClient("known/repo", SimpleNamespace(tags=[], card_data={}))
    assert ingest_single_dataset(
        config=object(), store=datasets_store, client=client, repo_id="ghost/repo",
    ) == "skipped_404"


def test_upsert_idempotent(datasets_store: HuggingFaceDatasetsStore) -> None:
    datasets_store.upsert_dataset(DatasetRecord(repo_id="o/d", likes=1))
    datasets_store.upsert_dataset(DatasetRecord(repo_id="o/d", likes=99))
    row = datasets_store.fetch_dataset("o/d")
    assert row is not None and row["likes"] == 99
    assert datasets_store.count("datasets") == 1


# ---------------------------------------------------------------------------
# Embedding text + payload
# ---------------------------------------------------------------------------


def test_row_to_text_combines_card_description_and_citation() -> None:
    row = {
        "repo_id": "huggingface/cats-dogs",
        "license": "cc-by-4.0",
        "card_data": '{"pretty_name": "Cats vs Dogs", "description": "Classification benchmark."}',
        "citation_text": "@misc{x, title = {Cats vs Dogs}}",
        "tags": ["image-classification", "size:25k"],
    }
    text = _row_to_text(row)
    assert "huggingface/cats-dogs" in text
    assert "Cats vs Dogs" in text
    assert "Classification benchmark" in text
    assert "cc-by-4.0" in text
    assert "@misc" in text
    assert "image-classification" in text


def test_row_to_payload_strips_to_filterable_signals() -> None:
    row = {
        "repo_id": "o/d",
        "author": "o",
        "license": "mit",
        "downloads": 100,
        "likes": 5,
        "paperswithcode_url": "https://paperswithcode.com/dataset/foo",
        # NOT in payload:
        "card_data": {"big": "blob"},
        "citation_text": "long citation",
        "tags": [],
    }
    payload = _row_to_payload(row)
    assert payload["entity_type"] == "datasets"
    assert payload["entity_id"] == "o/d"
    assert payload["paperswithcode_url"].startswith("https://paperswithcode.com")
    assert "card_data" not in payload
    assert "citation_text" not in payload
