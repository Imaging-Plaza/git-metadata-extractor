from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest

V2_TESTS_ROOT = Path(__file__).resolve().parent
V2_APP_STATE_FIELDS = (
    "v2_provider_set",
    "v2_orchestrator",
    "v2_github_provider",
    "v2_orcid_provider",
    "v2_infoscience_provider",
    "v2_ror_provider",
)


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


@pytest.fixture(autouse=True)
def _isolate_v2_runtime_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Ensure each test uses isolated local storage and mock providers by default."""
    monkeypatch.setenv("V2_GRAPH_DB_PATH", str(tmp_path / "v2_graph.db"))
    monkeypatch.setenv("CACHE_DB_PATH", str(tmp_path / "cache.db"))
    monkeypatch.setenv("V2_USE_MOCK_PROVIDERS", "true")
    # Most tests exercise deterministic rule-based behavior unless they opt into LLM explicitly.
    monkeypatch.setenv("V2_AGENT_RUNTIME_DEFAULT", "rule_based")
    # Default bearer token for `verify_token`. `tests/v2/test_auth.py`
    # overrides or deletes this var inside individual tests to exercise
    # the unauthorised / unconfigured paths.
    monkeypatch.setenv("API_TOKEN", "test-api-token")


@pytest.fixture(autouse=True)
def _isolate_main_app_state() -> Iterator[None]:
    """Prevent tests mutating src.api.app.state from leaking across tests."""
    from src.api import app as main_app  # noqa: PLC0415

    sentinel = object()
    original_values: dict[str, object] = {}
    for field in V2_APP_STATE_FIELDS:
        value = getattr(main_app.state, field, sentinel)
        original_values[field] = value
        if hasattr(main_app.state, field):
            delattr(main_app.state, field)

    yield

    for field, value in original_values.items():
        if value is sentinel:
            if hasattr(main_app.state, field):
                delattr(main_app.state, field)
            continue
        setattr(main_app.state, field, value)


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
