"""Unit tests for the huggingface_spaces index module."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.index.huggingface_spaces.embed.pipeline import _row_to_payload, _row_to_text
from src.index.huggingface_spaces.ingest.spaces import (
    _record_from_info,
    _runtime_fields,
    ingest_single_space,
)
from src.index.huggingface_spaces.models import SpaceRecord
from src.index.huggingface_spaces.storage.duckdb_store import (
    HuggingFaceSpacesStore,
)


class _FakeHFClient:
    def __init__(self, repo_id: str, info: Any | None) -> None:
        self._repo_id = repo_id
        self._info = info

    def space_info(self, repo_id: str, *, expand: tuple[str, ...]) -> Any | None:
        del expand
        if repo_id != self._repo_id:
            return None
        return self._info


@pytest.fixture()
def spaces_store(tmp_path: Path) -> HuggingFaceSpacesStore:
    store = HuggingFaceSpacesStore.open(tmp_path / "huggingface_spaces.duckdb")
    yield store
    store.close()


# ---------------------------------------------------------------------------
# runtime extraction
# ---------------------------------------------------------------------------


def test_runtime_fields_accepts_dict_shape() -> None:
    info = SimpleNamespace(runtime={"stage": "RUNNING", "hardware": "t4-small"})
    assert _runtime_fields(info) == ("RUNNING", "t4-small")


def test_runtime_fields_accepts_object_shape() -> None:
    runtime = SimpleNamespace(stage="SLEEPING", hardware=None, requested_hardware="cpu-basic")
    info = SimpleNamespace(runtime=runtime)
    assert _runtime_fields(info) == ("SLEEPING", "cpu-basic")


def test_runtime_fields_missing_returns_none() -> None:
    assert _runtime_fields(SimpleNamespace()) == (None, None)


# ---------------------------------------------------------------------------
# SpaceInfo → SpaceRecord
# ---------------------------------------------------------------------------


def test_record_from_info_populates_space_specific_fields() -> None:
    info = SimpleNamespace(
        author="alice",
        sha="abc",
        sdk="gradio",
        likes=12,
        tags=["audio"],
        card_data={"license": "mit", "title": "Audio demo"},
        runtime={"stage": "RUNNING", "hardware": "cpu-basic"},
    )
    record = _record_from_info("alice/audio-demo", info)
    assert record.repo_id == "https://huggingface.co/spaces/alice/audio-demo"
    assert record.sdk == "gradio"
    assert record.runtime_stage == "RUNNING"
    assert record.hardware == "cpu-basic"
    assert record.license == "mit"
    assert record.likes == 12


# ---------------------------------------------------------------------------
# DuckDB round-trip
# ---------------------------------------------------------------------------


def test_ingest_single_space_round_trips(
    spaces_store: HuggingFaceSpacesStore,
) -> None:
    info = SimpleNamespace(
        author="alice",
        sdk="streamlit",
        likes=5,
        tags=[],
        card_data={"license": "apache-2.0"},
        runtime={"stage": "RUNNING", "hardware": "t4-small"},
    )
    client = _FakeHFClient("alice/x", info)
    outcome = ingest_single_space(
        config=object(), store=spaces_store, client=client, repo_id="alice/x",
    )
    assert outcome == "ingested"
    row = spaces_store.fetch_space("https://huggingface.co/spaces/alice/x")
    assert row is not None
    assert row["sdk"] == "streamlit"
    assert row["hardware"] == "t4-small"


def test_ingest_skips_unknown_repo(spaces_store: HuggingFaceSpacesStore) -> None:
    client = _FakeHFClient("known/repo", SimpleNamespace(tags=[], card_data={}))
    assert ingest_single_space(
        config=object(), store=spaces_store, client=client, repo_id="ghost/repo",
    ) == "skipped_404"


def test_upsert_idempotent(spaces_store: HuggingFaceSpacesStore) -> None:
    spaces_store.upsert_space(SpaceRecord(repo_id="o/s", sdk="gradio", likes=1))
    spaces_store.upsert_space(SpaceRecord(repo_id="o/s", sdk="streamlit", likes=99))
    row = spaces_store.fetch_space("o/s")
    assert row is not None and row["sdk"] == "streamlit" and row["likes"] == 99
    assert spaces_store.count("spaces") == 1


# ---------------------------------------------------------------------------
# Embedding text + payload
# ---------------------------------------------------------------------------


def test_row_to_text_combines_card_and_sdk() -> None:
    row = {
        "repo_id": "alice/audio-demo",
        "sdk": "gradio",
        "card_data": '{"title": "Audio classifier", "short_description": "Demo of an audio model."}',
        "tags": ["audio", "demo"],
    }
    text = _row_to_text(row)
    assert "alice/audio-demo" in text
    assert "Audio classifier" in text
    assert "Demo of an audio model" in text
    assert "sdk: gradio" in text
    assert "audio, demo" in text


def test_row_to_payload_strips_to_filterable_signals() -> None:
    row = {
        "repo_id": "o/s",
        "author": "o",
        "sdk": "docker",
        "runtime_stage": "RUNNING",
        "hardware": "a10g-small",
        "license": "mit",
        "likes": 30,
        # NOT in payload:
        "card_data": {"big": "blob"},
        "tags": [],
    }
    payload = _row_to_payload(row)
    assert payload["entity_type"] == "spaces"
    assert payload["sdk"] == "docker"
    assert payload["runtime_stage"] == "RUNNING"
    assert "card_data" not in payload
    assert "tags" not in payload
