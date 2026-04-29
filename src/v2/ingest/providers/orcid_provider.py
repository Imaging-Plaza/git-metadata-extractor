from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import requests

from src.v2.ingest.cache import ProviderCache
from src.v2.ingest.providers.base import (
    ORCIDAffiliation,
    ORCIDProvider,
    ORCIDRecord,
    ProviderNotFoundError,
    ProviderPermissionError,
    ProviderRateLimitError,
)

if TYPE_CHECKING:
    from src.v2.ingest.providers.rate_limiter import RateLimiter

ORCID_PATTERN = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$")
HTTP_NOT_FOUND = 404
HTTP_FORBIDDEN = 403
HTTP_RATE_LIMIT = 429
ORCID_DIGIT_COUNT = 16
CHECKSUM_X_VALUE = 10


def _get_nested_value(payload: dict[str, Any], path: list[str]) -> str | None:
    current: Any = payload
    for segment in path:
        if not isinstance(current, dict):
            return None
        current = current.get(segment)
    if isinstance(current, str) and current:
        return current
    return None


def _normalize_date(date_payload: Any) -> str | None:
    if not isinstance(date_payload, dict):
        return None
    year = _get_nested_value(date_payload, ["year", "value"])
    month = _get_nested_value(date_payload, ["month", "value"]) or "01"
    day = _get_nested_value(date_payload, ["day", "value"]) or "01"
    if not year:
        return None
    return f"{year}-{month.zfill(2)}-{day.zfill(2)}"


def _extract_affiliations(payload: dict[str, Any], summary_key: str) -> list[ORCIDAffiliation]:
    affiliations: list[ORCIDAffiliation] = []
    groups = payload.get("affiliation-group")
    if not isinstance(groups, list):
        return affiliations

    for group in groups:
        if not isinstance(group, dict):
            continue
        summaries = group.get("summaries")
        if not isinstance(summaries, list):
            continue
        for summary in summaries:
            if not isinstance(summary, dict):
                continue
            summary_payload = summary.get(summary_key)
            if not isinstance(summary_payload, dict):
                continue

            organization = _get_nested_value(
                summary_payload,
                ["organization", "name"],
            ) or "Unknown Organization"
            department = _get_nested_value(
                summary_payload,
                ["department-name"],
            )
            role = _get_nested_value(
                summary_payload,
                ["role-title"],
            )
            start_date = _normalize_date(summary_payload.get("start-date"))
            end_date = _normalize_date(summary_payload.get("end-date"))

            affiliations.append(
                ORCIDAffiliation(
                    organization=organization,
                    department=department,
                    role=role,
                    start_date=start_date,
                    end_date=end_date,
                ),
            )
    return affiliations


class RealORCIDProvider(ORCIDProvider):
    """Production ORCID provider using public ORCID API endpoints."""

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        base_url: str = "https://pub.orcid.org/v3.0",
        timeout: int = 20,
        rate_limiter: RateLimiter | None = None,
        cache: ProviderCache | None = None,
    ) -> None:
        super().__init__(provider_name="orcid", rate_limiter=rate_limiter)
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._cache = cache

    def _http_client(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
        return self._session

    def _request(self, endpoint: str) -> dict[str, Any]:
        response = self._run_with_rate_limit(
            lambda: self._http_client().get(
                f"{self._base_url}{endpoint}",
                headers={"Accept": "application/json"},
                timeout=self._timeout,
            ),
        )
        if response.status_code == HTTP_NOT_FOUND:
            message = "ORCID record not found"
            raise ProviderNotFoundError(message)
        if response.status_code == HTTP_FORBIDDEN:
            message = "ORCID request forbidden"
            raise ProviderPermissionError(message)
        if response.status_code == HTTP_RATE_LIMIT:
            message = "ORCID rate limit reached"
            raise ProviderRateLimitError(message)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise TypeError
        return payload

    @staticmethod
    def _has_valid_checksum(orcid_id: str) -> bool:
        digits = orcid_id.replace("-", "")
        if len(digits) != ORCID_DIGIT_COUNT:
            return False

        total = 0
        for character in digits[:15]:
            if not character.isdigit():
                return False
            total = (total + int(character)) * 2

        remainder = total % 11
        check_value = (12 - remainder) % 11
        expected = "X" if check_value == CHECKSUM_X_VALUE else str(check_value)
        return digits[-1] == expected

    @classmethod
    def _normalize_orcid(cls, orcid_id: str) -> str:
        candidate = orcid_id.strip()
        if candidate.lower().startswith("https://orcid.org/"):
            candidate = candidate.rsplit("/", maxsplit=1)[-1]
        candidate = candidate.upper()
        if not ORCID_PATTERN.fullmatch(candidate):
            message = f"Invalid ORCID format: {orcid_id}"
            raise ValueError(message)
        if not cls._has_valid_checksum(candidate):
            message = f"Invalid ORCID checksum: {orcid_id}"
            raise ValueError(message)
        return candidate

    def get_person_by_orcid(self, orcid_id: str) -> ORCIDRecord:
        normalized_orcid = self._normalize_orcid(orcid_id)

        def _fetch() -> ORCIDRecord:
            person_payload = self._request(f"/{normalized_orcid}/person")
            employment_payload = self._request(f"/{normalized_orcid}/employments")
            education_payload = self._request(f"/{normalized_orcid}/educations")

            given_name = _get_nested_value(person_payload, ["name", "given-names", "value"])
            family_name = _get_nested_value(person_payload, ["name", "family-name", "value"])
            name_parts = [part for part in [given_name, family_name] if part]
            full_name = " ".join(name_parts) if name_parts else normalized_orcid

            employment = _extract_affiliations(employment_payload, "employment-summary")
            education = _extract_affiliations(education_payload, "education-summary")
            affiliations = sorted(
                {
                    affiliation["organization"]
                    for affiliation in [*employment, *education]
                    if affiliation.get("organization")
                },
            )

            return ORCIDRecord(
                orcid_id=normalized_orcid,
                name=full_name,
                employment=employment,
                education=education,
                affiliations=affiliations,
            )

        if self._cache is None:
            return _fetch()
        key = ProviderCache.make_key("orcid", "get_person_by_orcid", orcid=normalized_orcid)
        return self._cache.get_or_set(
            key,
            _fetch,
            label=f"orcid.get_person_by_orcid({normalized_orcid})",
        )
