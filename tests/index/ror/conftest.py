from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def mini_dump():
    """Hand-crafted 6-record ROR dump used across filter/document/dump_index tests."""
    return json.loads((FIXTURES / "dump_mini.json").read_text(encoding="utf-8"))


@pytest.fixture
def mini_dump_path():
    return FIXTURES / "dump_mini.json"
