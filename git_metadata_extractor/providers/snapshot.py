"""Primary scholarly adapters over the sources service's strict snapshot API."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from git_metadata_extractor.providers.base import (
    InfoscienceProvider,
    InfosciencePublicationRecord,
    ORCIDProvider,
    ORCIDRecord,
    ORCIDSearchHit,
    ProviderError,
    RORProvider,
)
from git_metadata_extractor.providers.sources_service import record_source_gap

if TYPE_CHECKING:
    from git_metadata_extractor.providers.sources_service import SourcesClient


class _SnapshotProvider:
    provider_name: str

    def __init__(self, client: SourcesClient):
        super().__init__()
        self.client = client

    def _record(self, method: str, payload: dict) -> dict[str, Any]:
        result = self.client.snapshot_lookup(self.provider_name, method, payload)
        if not isinstance(result, dict):
            message = f"Invalid snapshot response for {self.provider_name}.{method}"
            record_source_gap(message)
            raise ProviderError(message)
        return result

    def _records(self, method: str, payload: dict) -> list[dict[str, Any]]:
        result = self.client.snapshot_lookup(self.provider_name, method, payload)
        if not isinstance(result, list) or any(not isinstance(row, dict) for row in result):
            message = f"Invalid snapshot response for {self.provider_name}.{method}"
            record_source_gap(message)
            raise ProviderError(message)
        return result


class SnapshotRORProvider(_SnapshotProvider, RORProvider):
    provider_name = "ror"

    def get_organization(self, ror_id: str) -> dict[str, Any]:
        return self._record("get_organization", {"ror_id": ror_id})

    def search_organizations(self, query: str) -> list[dict[str, Any]]:
        return self._records("search_organizations", {"query": query})


class SnapshotORCIDProvider(_SnapshotProvider, ORCIDProvider):
    provider_name = "orcid"

    def get_person_by_orcid(self, orcid_id: str) -> ORCIDRecord:
        return cast("ORCIDRecord", self._record("get_person_by_orcid", {"orcid_id": orcid_id}))

    def search_persons(self, query: str, *, rows: int = 50, start: int = 0) -> list[ORCIDSearchHit]:
        return cast("list[ORCIDSearchHit]", self._records(
            "search_persons", {"query": query, "rows": rows, "start": start},
        ))


class SnapshotInfoscienceProvider(_SnapshotProvider, InfoscienceProvider):
    provider_name = "infoscience"

    def search_person(self, query: str) -> list[dict[str, Any]]:
        return self._records("search_person", {"query": query})

    def search_orgunit(self, query: str) -> list[dict[str, Any]]:
        return self._records("search_orgunit", {"query": query})

    def search_publications(self, query: str) -> list[InfosciencePublicationRecord]:
        return cast("list[InfosciencePublicationRecord]", self._records("search_publications", {"query": query}))
