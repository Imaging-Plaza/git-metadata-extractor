"""Test fixtures for the Infoscience indexer."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def article_json() -> dict:
    return json.loads((FIXTURES / "article_with_matches.json").read_text(encoding="utf-8"))


@pytest.fixture
def person_json() -> dict:
    return json.loads((FIXTURES / "person.json").read_text(encoding="utf-8"))


@pytest.fixture
def organization_json() -> dict:
    return json.loads((FIXTURES / "organization.json").read_text(encoding="utf-8"))


@pytest.fixture
def sample_extracted_text() -> str:
    return (FIXTURES / "sample_extracted.txt").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect INDEX_DATA_DIR per test so on-disk state doesn't leak."""
    monkeypatch.setenv("INDEX_DATA_DIR", str(tmp_path))
    return tmp_path
