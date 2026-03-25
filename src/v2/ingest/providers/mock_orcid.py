from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

from src.v2.ingest.providers.base import ORCIDProvider, ORCIDRecord, ProviderNotFoundError

ORCID_PATTERN = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$")
REQUIRED_RECORD_FIELDS = {"orcid_id", "name", "employment", "education", "affiliations"}
ORCID_DIGIT_COUNT = 16
CHECKSUM_X_VALUE = 10


class MockORCIDProvider(ORCIDProvider):
    """Fixture-backed ORCID provider for v2 pipeline testing."""

    def __init__(self, fixture_root: Path | None = None) -> None:
        self._fixture_root = fixture_root or self._default_fixture_root()

        self._record_by_orcid = {
            record["orcid_id"]: record
            for record in [
                self._load_record_fixture("valid_record"),
                self._load_record_fixture("no_affiliations"),
                self._load_record_fixture("multiple_employment"),
            ]
        }
        # Keep malformed/invalid fixture on disk for test coverage.
        self._load_json_fixture("invalid_checksum")

    @staticmethod
    def _default_fixture_root() -> Path:
        return (
            Path(__file__).resolve().parents[4]
            / "tests"
            / "v2"
            / "fixtures"
            / "providers"
            / "orcid"
        )

    def _load_json_fixture(self, fixture_name: str) -> dict[str, Any]:
        fixture_path = self._fixture_root / f"{fixture_name}.json"
        with fixture_path.open(encoding="utf-8") as fixture_file:
            payload = json.load(fixture_file)

        if not isinstance(payload, dict):
            raise TypeError
        return payload

    def _load_record_fixture(self, fixture_name: str) -> ORCIDRecord:
        payload = self._load_json_fixture(fixture_name)
        if not REQUIRED_RECORD_FIELDS.issubset(payload):
            message = f"Missing ORCID record fields in fixture '{fixture_name}'"
            raise ValueError(message)

        if not isinstance(payload["employment"], list):
            raise TypeError
        if not isinstance(payload["education"], list):
            raise TypeError
        if not isinstance(payload["affiliations"], list):
            raise TypeError

        return cast("ORCIDRecord", payload)

    @staticmethod
    def _has_valid_checksum(orcid_id: str) -> bool:
        digits = orcid_id.replace("-", "")
        if len(digits) != ORCID_DIGIT_COUNT:
            return False

        total = 0
        for char in digits[:15]:
            if not char.isdigit():
                return False
            total = (total + int(char)) * 2

        remainder = total % 11
        check_value = (12 - remainder) % 11
        expected = "X" if check_value == CHECKSUM_X_VALUE else str(check_value)
        return digits[-1] == expected

    @staticmethod
    def _normalize_orcid(orcid_id: str) -> str:
        candidate = orcid_id.strip()
        if candidate.lower().startswith("https://orcid.org/"):
            candidate = candidate.rsplit("/", maxsplit=1)[-1]

        normalized = candidate.upper()
        if not ORCID_PATTERN.fullmatch(normalized):
            message = f"Invalid ORCID format: {orcid_id}"
            raise ValueError(message)
        if not MockORCIDProvider._has_valid_checksum(normalized):
            message = f"Invalid ORCID checksum: {orcid_id}"
            raise ValueError(message)

        return normalized

    def get_person_by_orcid(self, orcid_id: str) -> ORCIDRecord:
        normalized_orcid = self._normalize_orcid(orcid_id)
        record = self._record_by_orcid.get(normalized_orcid)
        if record is None:
            message = f"ORCID record not found: {normalized_orcid}"
            raise ProviderNotFoundError(message)

        return deepcopy(record)
