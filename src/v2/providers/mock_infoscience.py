from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from src.v2.providers.base import InfoscienceProvider, InfosciencePublicationRecord


class MockInfoscienceProvider(InfoscienceProvider):
    """Fixture-backed Infoscience provider for v2 pipeline testing."""

    def __init__(self, fixture_root: Path | None = None) -> None:
        self._fixture_root = fixture_root or self._default_fixture_root()
        self._person_single_hit = self._load_json_fixture("person_single_hit")
        self._person_multi_hit = self._load_json_fixture("person_multi_hit")
        self._orgunit_result = self._load_json_fixture("orgunit_result")
        self._publication_result = self._load_json_fixture("publication_result")
        self._empty_result = self._load_json_fixture("empty_result")

    @staticmethod
    def _default_fixture_root() -> Path:
        return (
            Path(__file__).resolve().parents[3]
            / "tests"
            / "v2"
            / "fixtures"
            / "providers"
            / "infoscience"
        )

    def _load_json_fixture(self, fixture_name: str) -> dict[str, Any]:
        fixture_path = self._fixture_root / f"{fixture_name}.json"
        with fixture_path.open(encoding="utf-8") as fixture_file:
            payload = json.load(fixture_file)

        if not isinstance(payload, dict):
            raise TypeError
        return payload

    @staticmethod
    def _extract_results(payload: dict[str, Any]) -> list[dict[str, Any]]:
        results = payload.get("results")
        if isinstance(results, list):
            return deepcopy(results)
        return []

    @staticmethod
    def _as_string(value: Any) -> str | None:
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _as_string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, str) and item]

    @classmethod
    def _normalize_publication(cls, publication: dict[str, Any]) -> InfosciencePublicationRecord:
        publication_date = cls._as_string(publication.get("publicationDate"))
        if publication_date is None:
            publication_date = cls._as_string(publication.get("publication_date"))

        source_organization = cls._as_string(publication.get("sourceOrganization"))
        if source_organization is None:
            source_organization = cls._as_string(publication.get("lab"))

        return {
            "infosciencePublicationIdentifier": cls._as_string(
                publication.get("infosciencePublicationIdentifier"),
            )
            or cls._as_string(publication.get("uuid")),
            "title": cls._as_string(publication.get("title")),
            "authors": cls._as_string_list(publication.get("authors")),
            "publicationDate": publication_date,
            "doi": cls._as_string(publication.get("doi")),
            "url": cls._as_string(publication.get("url")),
            "sourceOrganization": source_organization,
        }

    def search_person(self, query: str) -> list[dict[str, Any]]:
        normalized_query = query.strip().lower()

        if normalized_query in {"alice smith", "single", "single-hit"}:
            return self._extract_results(self._person_single_hit)
        if normalized_query in {"smith", "ambiguous", "multi", "multi-hit"}:
            return self._extract_results(self._person_multi_hit)
        return self._extract_results(self._empty_result)

    def search_orgunit(self, query: str) -> list[dict[str, Any]]:
        normalized_query = query.strip().lower()

        if any(token in normalized_query for token in ("epfl", "enac", "laboratory")):
            return self._extract_results(self._orgunit_result)
        return self._extract_results(self._empty_result)

    def search_publications(self, query: str) -> list[InfosciencePublicationRecord]:
        normalized_query = query.strip().lower()

        if any(
            token in normalized_query
            for token in ("geodata", "structural", "10.5075/epfl-geodata-2024")
        ):
            publications = self._extract_results(self._publication_result)
            return [self._normalize_publication(publication) for publication in publications]
        return []
