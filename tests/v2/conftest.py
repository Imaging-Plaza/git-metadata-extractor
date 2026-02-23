from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pytest

V2_TESTS_ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class V2TestConfig:
    repo_root: Path
    tests_root: Path
    fixtures_root: Path
    schema_fixtures_root: Path
    golden_root: Path


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _normalize_json_name(name: str, *, schema: bool = False) -> str:
    if name.endswith(".json"):
        return name
    if schema:
        return f"{name}.schema.json"
    return f"{name}.json"


def _safe_join(base_dir: Path, subpath: Path, file_name: str) -> Path:
    base_resolved = base_dir.resolve()
    target_path = (base_resolved / subpath / file_name).resolve()
    if not target_path.is_relative_to(base_resolved):
        raise ValueError
    return target_path


def _item_path(item: pytest.Item) -> Path:
    if hasattr(item, "path"):
        return Path(str(item.path)).resolve()
    return Path(str(item.fspath)).resolve()


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if _item_path(item).is_relative_to(V2_TESTS_ROOT):
            item.add_marker(pytest.mark.v2)


@pytest.fixture(scope="session")
def v2_test_config() -> V2TestConfig:
    repo_root = V2_TESTS_ROOT.parents[1]
    fixtures_root = V2_TESTS_ROOT / "fixtures"
    return V2TestConfig(
        repo_root=repo_root,
        tests_root=V2_TESTS_ROOT,
        fixtures_root=fixtures_root,
        schema_fixtures_root=fixtures_root / "schema",
        golden_root=V2_TESTS_ROOT / "golden",
    )


@pytest.fixture(scope="session")
def load_schema(v2_test_config: V2TestConfig) -> Callable[[str, str], dict[str, Any]]:
    def _load(schema_type: str, schema_name: str) -> dict[str, Any]:
        if schema_type not in {"strict", "agent"}:
            raise ValueError

        schema_file_name = _normalize_json_name(schema_name, schema=True)
        schema_path = _safe_join(
            v2_test_config.schema_fixtures_root,
            Path(schema_type),
            schema_file_name,
        )
        if not schema_path.exists():
            raise FileNotFoundError(schema_path)

        parsed_schema = _load_json(schema_path)
        if not isinstance(parsed_schema, dict):
            raise TypeError
        return parsed_schema

    return _load


@pytest.fixture(scope="session")
def load_fixture(v2_test_config: V2TestConfig) -> Callable[[str, str], Any]:
    def _load(fixture_group: str, fixture_name: str) -> Any:
        fixture_file_name = _normalize_json_name(fixture_name)
        fixture_path = _safe_join(
            v2_test_config.fixtures_root,
            Path(fixture_group),
            fixture_file_name,
        )
        if not fixture_path.exists():
            raise FileNotFoundError(fixture_path)

        return _load_json(fixture_path)

    return _load


@pytest.fixture(scope="session")
def load_golden(v2_test_config: V2TestConfig) -> Callable[[str, str], Any]:
    def _load(golden_group: str, golden_name: str) -> Any:
        golden_file_name = _normalize_json_name(golden_name)
        golden_path = _safe_join(
            v2_test_config.golden_root,
            Path(golden_group),
            golden_file_name,
        )
        if not golden_path.exists():
            raise FileNotFoundError(golden_path)

        return _load_json(golden_path)

    return _load
