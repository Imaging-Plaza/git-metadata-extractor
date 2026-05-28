from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from src.v2.ingest.providers.base import ProviderNotFoundError, RORProvider


class MockRORProvider(RORProvider):
    """Fixture-backed ROR provider for v2 pipeline testing."""

    def __init__(self, fixture_root: Path | None = None) -> None:
        self._fixture_root = fixture_root or self._default_fixture_root()
        self._org_detail = self._load_json_fixture("org_detail")
        self._search_results = self._load_json_fixture("search_results")
        self._org_with_aliases = self._load_json_fixture("org_with_aliases")
        self._parent_org = self._load_json_fixture("parent_org")
        self._not_found = self._load_json_fixture("not_found")

    @staticmethod
    def _default_fixture_root() -> Path:
        return (
            Path(__file__).resolve().parents[4]
            / "tests"
            / "v2"
            / "fixtures"
            / "providers"
            / "ror"
        )

    def _load_json_fixture(self, fixture_name: str) -> dict[str, Any]:
        fixture_path = self._fixture_root / f"{fixture_name}.json"
        with fixture_path.open(encoding="utf-8") as fixture_file:
            payload = json.load(fixture_file)

        if not isinstance(payload, dict):
            raise TypeError
        return payload

    @staticmethod
    def _normalize_ror_id(ror_id: str) -> str:
        candidate = ror_id.strip()
        if candidate.startswith("https://ror.org/"):
            return candidate.removeprefix("https://ror.org/")
        return candidate

    def get_organization(self, ror_id: str) -> dict[str, Any]:
        normalized_ror_id = self._normalize_ror_id(ror_id)

        if normalized_ror_id == "02s376052":
            return deepcopy(self._org_detail["organization"])
        if normalized_ror_id == "05a28rw58":
            return deepcopy(self._parent_org["organization"])
        if normalized_ror_id == "03yrm5c26":
            return deepcopy(self._org_with_aliases["organization"])

        message = str(self._not_found.get("message", "ROR organization not found"))
        raise ProviderNotFoundError(message)

    def search_organizations(self, query: str) -> list[dict[str, Any]]:
        normalized_query = query.strip().lower()

        if any(token in normalized_query for token in {"epfl", "polytechnique"}):
            items = self._search_results.get("items")
            if isinstance(items, list):
                return deepcopy(items)
            return []

        if any(token in normalized_query for token in {"multilingual", "institut", "imi"}):
            return [deepcopy(self._org_with_aliases["organization"])]

        if "eth" in normalized_query:
            return [deepcopy(self._parent_org["organization"])]

        return []
