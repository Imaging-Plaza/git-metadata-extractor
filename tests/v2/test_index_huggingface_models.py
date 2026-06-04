"""Unit tests for the huggingface_models index module."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.index.huggingface_models.embed.pipeline import (
    _row_to_payload,
    _row_to_text,
)
from src.index.huggingface_models.ingest.models import (
    _extract_arxiv_dois,
    _record_from_info,
    ingest_single_model,
)
from src.index.huggingface_models.models import ModelRecord
from src.index.huggingface_models.storage.duckdb_store import (
    HuggingFaceModelsStore,
)


class _FakeHFClient:
    """Stand-in for HFClient.model_info."""

    def __init__(self, repo_id: str, info: Any | None) -> None:
        self._repo_id = repo_id
        self._info = info

    def model_info(self, repo_id: str, *, expand: tuple[str, ...]) -> Any | None:
        del expand
        if repo_id != self._repo_id:
            return None
        return self._info


@pytest.fixture()
def models_store(tmp_path: Path) -> HuggingFaceModelsStore:
    store = HuggingFaceModelsStore.open(tmp_path / "huggingface_models.duckdb")
    yield store
    store.close()


# ---------------------------------------------------------------------------
# arXiv extraction from tags
# ---------------------------------------------------------------------------


def test_extract_arxiv_dois_handles_versioned_and_unversioned_tags() -> None:
    tags = [
        "arxiv:2310.01234",
        "arxiv:2401.00001v2",
        "ARXIV:2305.12345",         # case-insensitive
        "license:mit",              # NOT arxiv → ignored
        "text-generation",          # NOT arxiv → ignored
    ]
    dois = _extract_arxiv_dois(tags)
    assert dois == [
        "https://doi.org/10.48550/arXiv.2310.01234",
        "https://doi.org/10.48550/arXiv.2401.00001",
        "https://doi.org/10.48550/arXiv.2305.12345",
    ]


def test_extract_arxiv_dois_skips_malformed() -> None:
    assert _extract_arxiv_dois(["arxiv:", "arxiv", "not-a-tag", 42]) == []


# ---------------------------------------------------------------------------
# ModelInfo → ModelRecord
# ---------------------------------------------------------------------------


def test_record_from_info_populates_all_fields() -> None:
    """SimpleNamespace stand-in for ModelInfo. The real class is from
    huggingface_hub; we just need attribute access in the same shape."""
    info = SimpleNamespace(
        author="bert-base-uncased-org",
        sha="abc123",
        pipeline_tag="text-classification",
        library_name="transformers",
        downloads=10_000,
        downloads_all_time=500_000,
        likes=250,
        gated=False,
        private=False,
        created_at=datetime(2024, 6, 1, tzinfo=timezone.utc),
        last_modified=datetime(2024, 11, 1, tzinfo=timezone.utc),
        tags=["arxiv:2310.01234", "license:mit", "text-classification"],
        card_data={"license": "mit", "base_model": "google/flan-t5-base"},
    )
    record = _record_from_info("hf-org/my-model", info)
    # v3.0.0: repo_id is the canonical HuggingFace URL.
    assert record.repo_id == "https://huggingface.co/hf-org/my-model"
    assert record.pipeline_tag == "text-classification"
    assert record.library_name == "transformers"
    assert record.license == "mit"
    assert record.downloads == 10_000
    assert record.downloads_all_time == 500_000
    assert record.likes == 250
    assert record.gated is False
    assert record.tags == ["arxiv:2310.01234", "license:mit", "text-classification"]
    assert record.base_models == ["google/flan-t5-base"]
    assert record.arxiv_dois == ["https://doi.org/10.48550/arXiv.2310.01234"]


def test_record_from_info_handles_card_data_to_dict() -> None:
    """HF's `ModelCardData` exposes `.to_dict()`. Make sure we use it
    when present."""

    class _CardData:
        def to_dict(self) -> dict[str, Any]:
            return {"license": "apache-2.0", "base_model": ["a", "b"]}

    info = SimpleNamespace(card_data=_CardData(), tags=[])
    record = _record_from_info("org/x", info)
    assert record.license == "apache-2.0"
    assert record.base_models == ["a", "b"]


# ---------------------------------------------------------------------------
# DuckDB round-trip
# ---------------------------------------------------------------------------


def test_ingest_single_model_round_trips(models_store: HuggingFaceModelsStore) -> None:
    info = SimpleNamespace(
        author="bert-base-uncased-org",
        pipeline_tag="text-classification",
        library_name="transformers",
        downloads=5,
        likes=2,
        tags=["text-classification"],
        card_data={"license": "mit"},
    )
    client = _FakeHFClient("hf-org/my-model", info)
    outcome = ingest_single_model(
        config=object(), store=models_store, client=client, repo_id="hf-org/my-model",
    )
    assert outcome == "ingested"
    # stored under the canonical URL id (the ingest input handle stays bare).
    row = models_store.fetch_model("https://huggingface.co/hf-org/my-model")
    assert row is not None
    assert row["repo_id"] == "https://huggingface.co/hf-org/my-model"
    assert row["pipeline_tag"] == "text-classification"
    assert row["license"] == "mit"


def test_ingest_skips_unknown_repo(models_store: HuggingFaceModelsStore) -> None:
    client = _FakeHFClient("known/repo", SimpleNamespace(tags=[], card_data={}))
    assert ingest_single_model(
        config=object(), store=models_store, client=client, repo_id="ghost/repo",
    ) == "skipped_404"


def test_upsert_idempotent(models_store: HuggingFaceModelsStore) -> None:
    models_store.upsert_model(ModelRecord(repo_id="o/m", downloads=10))
    models_store.upsert_model(ModelRecord(repo_id="o/m", downloads=20))
    row = models_store.fetch_model("o/m")
    assert row is not None and row["downloads"] == 20
    assert models_store.count("models") == 1


# ---------------------------------------------------------------------------
# Embedding text + payload
# ---------------------------------------------------------------------------


def test_row_to_text_combines_repo_id_and_tags() -> None:
    row = {
        "repo_id": "google/flan-t5-base",
        "library_name": "transformers",
        "pipeline_tag": "text2text-generation",
        "license": "apache-2.0",
        "card_data": '{"description": "FLAN-T5 paper"}',
        "tags": ["text2text-generation", "transformers"],
    }
    text = _row_to_text(row)
    assert "google/flan-t5-base" in text
    assert "transformers" in text
    assert "text2text-generation" in text
    assert "apache-2.0" in text
    assert "FLAN-T5 paper" in text
    assert "tags:" in text


def test_row_to_payload_strips_to_filterable_signals() -> None:
    row = {
        "repo_id": "o/m",
        "author": "o",
        "pipeline_tag": "text-classification",
        "library_name": "transformers",
        "license": "mit",
        "downloads": 1000,
        "likes": 50,
        # NOT in payload:
        "raw": {"big": "blob"},
        "card_data": {"long": "card"},
        "tags": [],
    }
    payload = _row_to_payload(row)
    assert payload["entity_type"] == "models"
    assert payload["entity_id"] == "o/m"
    assert payload["downloads"] == 1000
    assert "raw" not in payload
    assert "card_data" not in payload
    assert "tags" not in payload  # tags feed embedding only
