from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


def test_load_schema_returns_dict(
    load_schema: Callable[[str, str], dict[str, Any]],
) -> None:
    schema = load_schema("strict", "person")
    assert isinstance(schema, dict)


def test_load_fixture_supports_nested_groups(
    load_fixture: Callable[[str, str], Any],
) -> None:
    fixture = load_fixture("schema/strict", "person.schema")
    assert isinstance(fixture, dict)


def test_v2_test_config_paths_exist(v2_test_config: Any) -> None:
    assert v2_test_config.tests_root == Path(__file__).resolve().parent
    assert v2_test_config.fixtures_root.exists()
    assert v2_test_config.golden_root.exists()
