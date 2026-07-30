from __future__ import annotations

import calendar
import contextlib
import logging
import re
from datetime import date
from typing import TYPE_CHECKING, Any

import requests

logger = logging.getLogger(__name__)

from git_metadata_extractor.providers.cache import ProviderCache
from git_metadata_extractor.providers.base import (
    ORCIDAffiliation,
    ORCIDProvider,
    ORCIDRecord,
    ORCIDSearchHit,
    ProviderNotFoundError,
    ProviderPermissionError,
    ProviderRateLimitError,
)

EXPANDED_SEARCH_EDISMAX = (
    '{!edismax qf="given-and-family-names^50.0 family-name^10.0 '
    'given-names^10.0 credit-name^10.0 other-names^5.0 text^1.0" '
    'pf="given-and-family-names^50.0" '
    'bq="current-institution-affiliation-name:[* TO *]^100.0 '
    'past-institution-affiliation-name:[* TO *]^70" mm=1}'
)
EXPANDED_SEARCH_MAX_ROWS = 200

if TYPE_CHECKING:
    from git_metadata_extractor.providers.rate_limiter import RateLimiter

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
    """Convert an ORCID date payload (`{year, month, day}` substructures) to
    an ISO ``YYYY-MM-DD`` string.

    Missing month/day default to ``01``. ORCID accepts user-typed dates
    without strict validation, so impossible combinations (e.g.
    ``2000-09-31``) reach this function. The strategy:

    1. Try the literal year/month/day. If valid, return it.
    2. If invalid (e.g. day=31 in September), clamp the day to the last
       valid day of that month and return the clamped value. The user's
       intent ("end of September 2000") is preserved with 1-day drift,
       which is much better than losing the whole date.
    3. If still invalid (year+month combination is bogus, non-numeric
       components, etc.), return ``None``.
    """

    if not isinstance(date_payload, dict):
        return None
    year = _get_nested_value(date_payload, ["year", "value"])
    month = _get_nested_value(date_payload, ["month", "value"]) or "01"
    day = _get_nested_value(date_payload, ["day", "value"]) or "01"
    if not year:
        return None

    try:
        year_int, month_int, day_int = int(year), int(month), int(day)
    except (ValueError, TypeError):
        logger.info(
            "orcid_provider: dropped non-numeric date year=%r month=%r day=%r",
            year, month, day,
        )
        return None

    try:
        return date(year_int, month_int, day_int).isoformat()
    except ValueError:
        pass

    # Day overflow (e.g. Sept 31, Feb 30) — clamp to the last day of the month.
    try:
        last_day = calendar.monthrange(year_int, month_int)[1]
        clamped = date(year_int, month_int, last_day).isoformat()
        logger.info(
            "orcid_provider: clamped invalid day year=%d month=%d day=%d → day=%d",
            year_int, month_int, day_int, last_day,
        )
        return clamped
    except (ValueError, calendar.IllegalMonthError):
        logger.info(
            "orcid_provider: dropped unrecoverable date year=%d month=%d day=%d",
            year_int, month_int, day_int,
        )
        return None


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

    def close(self) -> None:
        """Close the pooled HTTP session if one was lazily created, so its
        urllib3 connection pool isn't leaked once per extraction (Bug 03)."""
        if self._session is not None:
            with contextlib.suppress(Exception):  # best-effort cleanup
                self._session.close()
            self._session = None

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
        """Return the bare-form ORCID for use as a path component
        against `pub.orcid.org`. Combines shape normalisation (via
        the shared `parse_orcid` helper) with mod-11 checksum
        validation that the shared helper deliberately doesn't do —
        bogus IDs should fail loudly when they reach a real provider,
        not silently pass through."""
        from git_metadata_extractor.canonicalization.orcid import parse_orcid

        candidate = parse_orcid(orcid_id)
        if candidate is None:
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

    def search_persons(
        self,
        query: str,
        *,
        rows: int = 50,
        start: int = 0,
    ) -> list[ORCIDSearchHit]:
        cleaned_query = query.strip()
        if not cleaned_query:
            return []
        bounded_rows = max(1, min(rows, EXPANDED_SEARCH_MAX_ROWS))
        bounded_start = max(0, start)

        def _fetch() -> list[ORCIDSearchHit]:
            payload = self._request(
                "/expanded-search/",
                params={
                    "q": f"{EXPANDED_SEARCH_EDISMAX}{cleaned_query}",
                    "start": bounded_start,
                    "rows": bounded_rows,
                },
            )
            results = payload.get("expanded-result")
            if not isinstance(results, list):
                return []
            return [
                _normalize_search_hit(item)
                for item in results
                if isinstance(item, dict)
            ]

        if self._cache is None:
            return _fetch()
        key = ProviderCache.make_key(
            "orcid",
            "search_persons",
            query=cleaned_query,
            rows=bounded_rows,
            start=bounded_start,
        )
        return self._cache.get_or_set(
            key,
            _fetch,
            label=f"orcid.search_persons({cleaned_query!r})",
        )


def _normalize_search_hit(item: dict[str, Any]) -> ORCIDSearchHit:
    def _str_or_none(value: Any) -> str | None:
        return value if isinstance(value, str) and value else None

    def _str_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [entry for entry in value if isinstance(entry, str) and entry]

    return ORCIDSearchHit(
        orcid_id=_str_or_none(item.get("orcid-id")),
        given_names=_str_or_none(item.get("given-names")),
        family_names=_str_or_none(item.get("family-names")),
        credit_name=_str_or_none(item.get("credit-name")),
        other_names=_str_list(item.get("other-name")),
        institution_names=_str_list(item.get("institution-name")),
        emails=_str_list(item.get("email")),
    )
