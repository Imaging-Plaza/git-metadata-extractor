from __future__ import annotations

from typing import TYPE_CHECKING, Any

import requests

from src.v2.providers.base import (
    ProviderNotFoundError,
    ProviderPermissionError,
    ProviderRateLimitError,
    RORProvider,
)

if TYPE_CHECKING:
    from src.v2.providers.rate_limiter import RateLimiter

HTTP_NOT_FOUND = 404
HTTP_FORBIDDEN = 403
HTTP_RATE_LIMIT = 429


def _first_name(names: list[dict[str, Any]]) -> str | None:
    for name_entry in names:
        if not isinstance(name_entry, dict):
            continue
        if "ror_display" in name_entry.get("types", []):
            value = name_entry.get("value")
            if isinstance(value, str) and value:
                return value
    for name_entry in names:
        if not isinstance(name_entry, dict):
            continue
        value = name_entry.get("value")
        if isinstance(value, str) and value:
            return value
    return None


def _normalize_relationships(relationships: list[dict[str, Any]]) -> dict[str, Any]:
    parent = None
    children: list[dict[str, Any]] = []

    for relationship in relationships:
        if not isinstance(relationship, dict):
            continue
        relation_type = str(relationship.get("type", "")).lower()
        relation_id = relationship.get("id")
        relation_label = relationship.get("label")
        relation_payload = {
            "id": relation_id,
            "name": relation_label,
        }
        if relation_type == "parent":
            parent = relation_payload
        if relation_type == "child":
            children.append(relation_payload)

    return {
        "parent": parent,
        "children": children,
    }


def _normalize_ror_organization(item: dict[str, Any]) -> dict[str, Any]:
    names = item.get("names")
    names_list = names if isinstance(names, list) else []
    aliases = [
        name.get("value")
        for name in names_list
        if isinstance(name, dict) and "alias" in name.get("types", [])
    ]

    acronyms = item.get("acronyms")
    acronyms_list = [value for value in acronyms if isinstance(value, str)] if isinstance(acronyms, list) else []

    types = item.get("types")
    type_names = []
    if isinstance(types, list):
        for organization_type in types:
            if isinstance(organization_type, dict):
                organization_type_value = organization_type.get("label")
                if isinstance(organization_type_value, str):
                    type_names.append(organization_type_value)
            elif isinstance(organization_type, str):
                type_names.append(organization_type)

    locations = item.get("locations")
    country_payload = {}
    if isinstance(locations, list) and locations:
        first_location = locations[0]
        if isinstance(first_location, dict):
            country = first_location.get("geonames_details", {}).get("country_name")
            country_code = first_location.get("geonames_details", {}).get("country_code")
            if country or country_code:
                country_payload = {
                    "country_name": country,
                    "country_code": country_code,
                }

    links = item.get("links")
    links_list = [value for value in links if isinstance(value, str)] if isinstance(links, list) else []
    relationships = item.get("relationships")
    relationships_payload = _normalize_relationships(
        relationships if isinstance(relationships, list) else [],
    )

    return {
        "id": item.get("id"),
        "name": _first_name(names_list),
        "aliases": aliases,
        "acronyms": acronyms_list,
        "types": type_names,
        "country": country_payload,
        "links": links_list,
        "relationships": relationships_payload,
    }


class RealRORProvider(RORProvider):
    """Production ROR provider wrapping ROR HTTP lookup endpoints."""

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        base_url: str = "https://api.ror.org/v2",
        timeout: int = 20,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        super().__init__(provider_name="ror", rate_limiter=rate_limiter)
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def _http_client(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
        return self._session

    def _request(
        self,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self._run_with_rate_limit(
            lambda: self._http_client().get(
                f"{self._base_url}{endpoint}",
                params=params,
                timeout=self._timeout,
            ),
        )
        if response.status_code == HTTP_NOT_FOUND:
            message = "ROR organization not found"
            raise ProviderNotFoundError(message)
        if response.status_code == HTTP_FORBIDDEN:
            message = "ROR request forbidden"
            raise ProviderPermissionError(message)
        if response.status_code == HTTP_RATE_LIMIT:
            message = "ROR rate limit reached"
            raise ProviderRateLimitError(message)
        response.raise_for_status()

        payload = response.json()
        if not isinstance(payload, dict):
            raise TypeError
        return payload

    @staticmethod
    def _normalize_ror_id(ror_id: str) -> str:
        candidate = ror_id.strip()
        if candidate.startswith("https://ror.org/"):
            return candidate.rsplit("/", maxsplit=1)[-1]
        return candidate

    def get_organization(self, ror_id: str) -> dict[str, Any]:
        normalized_id = self._normalize_ror_id(ror_id)
        payload = self._request(f"/organizations/{normalized_id}")
        return _normalize_ror_organization(payload)

    def search_organizations(self, query: str) -> list[dict[str, Any]]:
        payload = self._request("/organizations", params={"query": query})
        items = payload.get("items")
        if not isinstance(items, list):
            return []
        return [
            _normalize_ror_organization(item)
            for item in items
            if isinstance(item, dict)
        ]
