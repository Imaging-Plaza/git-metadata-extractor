"""Unit tests for the huggingface_papers index module.

Exercises the wire-input normaliser, the payload → PaperRecord
transform, DuckDB round-trip via ingest, and the embed-text
composition. No network calls — uses a `_FakeHFPapersClient` stub.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.index.huggingface_papers.embed.pipeline import (
    _row_to_payload,
    _row_to_text,
)
from src.index.huggingface_papers.ingest.hf_papers_client import (
    normalize_arxiv_id,
)
from src.index.huggingface_papers.ingest.papers import (
    _extract_authors,
    _record_from_payload,
    ingest_single_paper,
)
from src.index.huggingface_papers.models import PaperRecord
from src.index.huggingface_papers.storage.duckdb_store import (
    HuggingFacePapersStore,
)


# ---------------------------------------------------------------------------
# arXiv id normalisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2310.01234", "2310.01234"),
        ("  2310.01234  ", "2310.01234"),
        ("2310.01234v2", "2310.01234"),
        ("2310.01234V3", "2310.01234"),
        ("https://arxiv.org/abs/2310.01234", "2310.01234"),
        ("https://arxiv.org/abs/2310.01234v1", "2310.01234"),
        ("https://arxiv.org/pdf/2310.01234.pdf", "2310.01234"),
        ("https://huggingface.co/papers/2310.01234", "2310.01234"),
        ("https://huggingface.co/papers/2310.01234v2", "2310.01234"),
        ("arxiv:2310.01234", "2310.01234"),
        ("ARXIV:2310.01234V1", "2310.01234"),
        ("10.48550/arXiv.2310.01234", "2310.01234"),
        ("https://doi.org/10.48550/arXiv.2310.01234", "2310.01234"),
    ],
)
def test_normalize_arxiv_id_accepts_every_wire_shape(raw: str, expected: str) -> None:
    assert normalize_arxiv_id(raw) == expected


@pytest.mark.parametrize("bad", [None, "", "   ", 42, ["2310.01234"], "v2", "/"])
def test_normalize_arxiv_id_rejects_garbage(bad) -> None:
    assert normalize_arxiv_id(bad) is None


# ---------------------------------------------------------------------------
# Payload → PaperRecord
# ---------------------------------------------------------------------------


def test_extract_authors_flattens_nested_user_block() -> None:
    payload = {
        "paper": {
            "authors": [
                {
                    "name": "Alice Anderson",
                    "hidden": False,
                    "user": {"user": "alice", "affiliation": "EPFL"},
                },
                {"name": "Bob Builder", "hidden": True},
                # Should be filtered (no name):
                {"hidden": False},
            ],
        },
    }
    authors = _extract_authors(payload)
    assert len(authors) == 2
    assert authors[0].name == "Alice Anderson"
    assert authors[0].user_id == "alice"
    assert authors[0].affiliation == "EPFL"
    assert authors[1].name == "Bob Builder"
    assert authors[1].hidden is True
    assert authors[1].user_id is None


def test_record_from_payload_handles_nested_paper_block() -> None:
    payload = {
        "paper": {
            "title": "Attention Is All You Need (Retrospective)",
            "summary": "We revisit transformers.",
            "authors": [{"name": "Alice", "user": {"user": "alice"}}],
            "publishedAt": "2023-10-01T00:00:00Z",
        },
        "upvotes": 142,
        "numComments": 7,
        "aiSummary": "TL;DR: still attention.",
        "aiKeywords": ["transformer", "attention", "retrospective"],
        "thumbnail": "https://example.com/thumb.png",
        "isAuthorParticipating": True,
        "linkedModels": [{"repo_id": "huggingface/llama"}],
        "linkedDatasets": [],
    }

    record = _record_from_payload("2310.01234", payload)

    assert record.arxiv_id == "2310.01234"
    assert record.title == "Attention Is All You Need (Retrospective)"
    assert record.summary == "We revisit transformers."
    assert record.doi == "10.48550/arXiv.2310.01234"
    assert record.upvotes == 142
    assert record.num_comments == 7
    assert record.ai_summary == "TL;DR: still attention."
    assert record.ai_keywords == ["transformer", "attention", "retrospective"]
    assert record.linked_models == [{"repo_id": "huggingface/llama"}]
    assert record.linked_datasets == []
    assert record.is_author_participating is True
    assert record.published_at is not None
    assert len(record.authors) == 1


def test_record_from_payload_tolerates_flat_payload() -> None:
    """Older or simplified payloads put title/summary at the top level."""
    payload = {
        "title": "Flat paper",
        "summary": "No nested block.",
        "upvotes": 0,
        "publishedAt": "2024-01-15T00:00:00Z",
    }
    record = _record_from_payload("2401.00001", payload)
    assert record.title == "Flat paper"
    assert record.summary == "No nested block."
    assert record.upvotes == 0


# ---------------------------------------------------------------------------
# DuckDB round-trip
# ---------------------------------------------------------------------------


class _FakeHFPapersClient:
    def __init__(self, arxiv_id: str, payload: dict[str, Any] | None) -> None:
        self._arxiv_id = arxiv_id
        self._payload = payload

    def get_paper(self, arxiv_id: str) -> dict[str, Any] | None:
        if arxiv_id != self._arxiv_id:
            return None
        return self._payload


@pytest.fixture()
def papers_store(tmp_path: Path) -> HuggingFacePapersStore:
    store = HuggingFacePapersStore.open(tmp_path / "huggingface_papers.duckdb")
    yield store
    store.close()


def test_ingest_single_paper_round_trips_through_duckdb(
    papers_store: HuggingFacePapersStore,
) -> None:
    payload = {
        "paper": {
            "title": "Graph Neural Networks for Molecular Property Prediction",
            "summary": "We propose...",
            "authors": [
                {"name": "Researcher A", "user": {"user": "researcher-a"}},
            ],
            "publishedAt": "2024-05-01T12:00:00Z",
        },
        "upvotes": 23,
        "aiKeywords": ["GNN", "molecules"],
    }
    client = _FakeHFPapersClient("2405.00001", payload)

    outcome = ingest_single_paper(
        config=object(), store=papers_store, client=client, arxiv_id="2405.00001",
    )

    assert outcome == "ingested"
    row = papers_store.fetch_paper("2405.00001")
    assert row is not None
    assert row["arxiv_id"] == "2405.00001"
    assert row["title"].startswith("Graph Neural")
    assert row["upvotes"] == 23
    assert row["doi"] == "10.48550/arXiv.2405.00001"


def test_ingest_single_paper_skips_unknown_arxiv_id(
    papers_store: HuggingFacePapersStore,
) -> None:
    client = _FakeHFPapersClient("2405.00001", {"paper": {"title": "X"}})
    outcome = ingest_single_paper(
        config=object(), store=papers_store, client=client, arxiv_id="9999.99999",
    )
    assert outcome == "skipped_404"
    assert papers_store.fetch_paper("9999.99999") is None


def test_ingest_single_paper_skips_title_less_payload(
    papers_store: HuggingFacePapersStore,
) -> None:
    client = _FakeHFPapersClient("2405.00001", {"paper": {"summary": "abstract only"}})
    outcome = ingest_single_paper(
        config=object(), store=papers_store, client=client, arxiv_id="2405.00001",
    )
    assert outcome == "skipped_404"


def test_count_starts_at_zero_and_grows(papers_store: HuggingFacePapersStore) -> None:
    assert papers_store.count("papers") == 0
    papers_store.upsert_paper(
        PaperRecord(arxiv_id="2401.00001", title="Test paper"),
    )
    assert papers_store.count("papers") == 1


def test_upsert_paper_is_idempotent_on_arxiv_id(
    papers_store: HuggingFacePapersStore,
) -> None:
    papers_store.upsert_paper(
        PaperRecord(arxiv_id="2401.00001", title="Initial", upvotes=5),
    )
    papers_store.upsert_paper(
        PaperRecord(arxiv_id="2401.00001", title="Updated", upvotes=12),
    )
    row = papers_store.fetch_paper("2401.00001")
    assert row is not None
    assert row["title"] == "Updated"
    assert row["upvotes"] == 12
    assert papers_store.count("papers") == 1


# ---------------------------------------------------------------------------
# Embedding text composition + payload split
# ---------------------------------------------------------------------------


def test_row_to_text_combines_title_aisummary_abstract_keywords_authors() -> None:
    row = {
        "arxiv_id": "2405.00001",
        "title": "Graph Neural Networks for Molecular Property Prediction",
        "ai_summary": "GNNs beat baselines on QM9.",
        "summary": "We propose a new architecture...",
        "ai_keywords": '["GNN", "molecules", "QM9"]',     # JSON-encoded list
        "authors": '[{"name": "Alice A"}, {"name": "Bob B"}]',
    }
    text = _row_to_text(row)
    assert "Graph Neural Networks" in text
    assert "GNNs beat baselines" in text
    assert "We propose a new architecture" in text
    assert "keywords:" in text and "GNN" in text and "QM9" in text
    assert "authors:" in text and "Alice A" in text and "Bob B" in text


def test_row_to_text_handles_authors_as_python_list_not_json() -> None:
    """DuckDB sometimes returns JSON columns as already-deserialised
    Python objects depending on the driver. The composer must accept
    both shapes."""
    row = {
        "arxiv_id": "2401.00001",
        "title": "T",
        "ai_keywords": ["kw1", "kw2"],          # Python list, not string
        "authors": [{"name": "Author A"}],      # Python list
    }
    text = _row_to_text(row)
    assert "kw1, kw2" in text
    assert "Author A" in text


def test_row_to_text_skips_empty_sections() -> None:
    row = {
        "arxiv_id": "2401.00001",
        "title": "Just a title",
        "summary": None,
        "ai_summary": "   ",  # whitespace-only
        "ai_keywords": None,
        "authors": None,
    }
    text = _row_to_text(row)
    assert text == "Just a title"


def test_row_to_payload_strips_to_filterable_signals() -> None:
    row = {
        "arxiv_id": "2310.01234",
        "doi": "10.48550/arXiv.2310.01234",
        "title": "Attention Is All You Need (Retrospective)",
        "upvotes": 142,
        "num_comments": 7,
        "published_at": None,
        # Should NOT be in the Qdrant payload (DuckDB-only):
        "summary": "long abstract...",
        "ai_summary": "TL;DR...",
        "raw": {"big": "blob"},
        "authors": [],
    }
    payload = _row_to_payload(row)
    assert payload["entity_type"] == "papers"
    assert payload["entity_id"] == "2310.01234"
    assert payload["arxiv_id"] == "2310.01234"
    assert payload["doi"] == "10.48550/arXiv.2310.01234"
    assert payload["upvotes"] == 142
    assert "summary" not in payload      # feeds embedding only
    assert "ai_summary" not in payload   # feeds embedding only
    assert "raw" not in payload
    assert "authors" not in payload
