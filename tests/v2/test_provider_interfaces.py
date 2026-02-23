from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from src.v2.providers import (
    BaseProvider,
    GitHubProvider,
    InfoscienceProvider,
    MockGitHubProvider,
    MockInfoscienceProvider,
    MockORCIDProvider,
    MockRORProvider,
    ORCIDProvider,
    RORProvider,
    get_provider,
)
from src.v2.providers.github_provider import RealGitHubProvider
from src.v2.providers.infoscience_provider import RealInfoscienceProvider
from src.v2.providers.orcid_provider import RealORCIDProvider
from src.v2.providers.ror_provider import RealRORProvider

STATUS_ERROR_THRESHOLD = 400


@dataclass
class _Commits:
    total: int


@dataclass
class _GitAuthor:
    name: str
    email: str | None
    id: str
    commits: _Commits


class _FakeResponse:
    def __init__(self, *, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= STATUS_ERROR_THRESHOLD:
            message = f"HTTP {self.status_code}"
            raise RuntimeError(message)

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeSession:
    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> _FakeResponse:
        del params, timeout, headers
        if url.endswith("/0000-0002-1825-0097/person"):
            return _FakeResponse(
                status_code=200,
                payload={
                    "name": {
                        "given-names": {"value": "Alice"},
                        "family-name": {"value": "Example"},
                    },
                },
            )
        if url.endswith("/0000-0002-1825-0097/employments"):
            return _FakeResponse(
                status_code=200,
                payload={
                    "affiliation-group": [
                        {
                            "summaries": [
                                {
                                    "employment-summary": {
                                        "organization": {"name": "EPFL"},
                                        "department-name": "School of Engineering",
                                        "role-title": "Research Engineer",
                                        "start-date": {
                                            "year": {"value": "2021"},
                                            "month": {"value": "01"},
                                            "day": {"value": "01"},
                                        },
                                    },
                                },
                            ],
                        },
                    ],
                },
            )
        if url.endswith("/0000-0002-1825-0097/educations"):
            return _FakeResponse(
                status_code=200,
                payload={"affiliation-group": []},
            )
        if url.endswith("/organizations/02s376052"):
            return _FakeResponse(
                status_code=200,
                payload={
                    "id": "https://ror.org/02s376052",
                    "names": [{"value": "EPFL", "types": ["ror_display"]}],
                    "acronyms": ["EPFL"],
                    "types": [{"label": "Education"}],
                    "locations": [
                        {
                            "geonames_details": {
                                "country_name": "Switzerland",
                                "country_code": "CH",
                            },
                        },
                    ],
                    "links": ["https://www.epfl.ch"],
                    "relationships": [],
                },
            )
        if url.endswith("/organizations"):
            return _FakeResponse(
                status_code=200,
                payload={
                    "items": [
                        {
                            "id": "https://ror.org/02s376052",
                            "names": [{"value": "EPFL", "types": ["ror_display"]}],
                            "acronyms": ["EPFL"],
                            "types": [{"label": "Education"}],
                            "locations": [],
                            "links": [],
                            "relationships": [],
                        },
                    ],
                },
            )
        return _FakeResponse(status_code=404, payload={"error": "not found"})


def _build_real_github_provider() -> RealGitHubProvider:
    async def _repository_context_loader(_: str) -> dict[str, Any]:
        return {
            "git_authors": [
                _GitAuthor(
                    name="The Octocat",
                    email="octocat@github.com",
                    id="octocat",
                    commits=_Commits(total=3),
                ),
            ],
        }

    return RealGitHubProvider(
        gimie_extractor=lambda _url, _format: {
            "@graph": [
                {
                    "@type": "schema:SoftwareSourceCode",
                    "schema:name": "Hello-World",
                    "schema:codeRepository": "https://github.com/octocat/Hello-World",
                    "schema:programmingLanguage": ["Python"],
                },
            ],
        },
        user_lookup=lambda username: {"login": username, "name": "The Octocat"},
        organization_lookup=lambda org_name: {"login": org_name, "name": "GitHub"},
        repository_context_loader=_repository_context_loader,
    )


def _build_real_infoscience_provider() -> RealInfoscienceProvider:
    async def _search_authors(_query: str, _max_results: int) -> dict[str, Any]:
        return {
            "authors": [
                {
                    "uuid": "1f0b2b90-9e33-4f9a-9b14-8468f89f2e4d",
                    "name": "Alice Smith",
                    "orcid": "0000-0002-1825-0097",
                    "affiliation": "EPFL",
                    "profile_url": "https://infoscience.epfl.ch/entities/person/1f0b2b90-9e33-4f9a-9b14-8468f89f2e4d",
                },
            ],
        }

    async def _search_labs(_query: str, _max_results: int) -> dict[str, Any]:
        return {
            "labs": [
                {
                    "uuid": "8072fc98-7fc8-44cb-b8f9-805f9b0725f3",
                    "name": "ENAC",
                    "parent_organization": "EPFL",
                    "url": "https://infoscience.epfl.ch/entities/orgunit/8072fc98-7fc8-44cb-b8f9-805f9b0725f3",
                },
            ],
        }

    async def _search_publications(_query: str, _max_results: int) -> dict[str, Any]:
        return {
            "publications": [
                {
                    "uuid": "123",
                    "title": "Metadata at Scale",
                    "doi": "10.1234/example",
                    "url": "https://infoscience.epfl.ch/entities/publication/123",
                },
            ],
        }

    return RealInfoscienceProvider(
        search_authors_func=_search_authors,
        search_labs_func=_search_labs,
        search_publications_func=_search_publications,
    )


def test_real_providers_implement_base_interfaces() -> None:
    real_github = _build_real_github_provider()
    real_infoscience = _build_real_infoscience_provider()
    real_orcid = RealORCIDProvider(session=_FakeSession())
    real_ror = RealRORProvider(session=_FakeSession())

    assert isinstance(real_github, BaseProvider)
    assert isinstance(real_infoscience, BaseProvider)
    assert isinstance(real_orcid, BaseProvider)
    assert isinstance(real_ror, BaseProvider)

    assert isinstance(real_github, GitHubProvider)
    assert isinstance(real_infoscience, InfoscienceProvider)
    assert isinstance(real_orcid, ORCIDProvider)
    assert isinstance(real_ror, RORProvider)

    assert not RealGitHubProvider.__abstractmethods__
    assert not RealInfoscienceProvider.__abstractmethods__
    assert not RealORCIDProvider.__abstractmethods__
    assert not RealRORProvider.__abstractmethods__


def test_real_providers_execute_all_interface_methods_without_notimplementederror() -> None:
    real_github = _build_real_github_provider()
    real_infoscience = _build_real_infoscience_provider()
    real_orcid = RealORCIDProvider(session=_FakeSession())
    real_ror = RealRORProvider(session=_FakeSession())

    repository = real_github.get_repository("octocat/Hello-World")
    user = real_github.get_user("octocat")
    organization = real_github.get_organization("github")
    contributors = real_github.get_contributors("octocat/Hello-World")
    languages = real_github.get_languages("octocat/Hello-World")

    assert repository["full_name"] == "octocat/Hello-World"
    assert user["login"] == "octocat"
    assert organization["login"] == "github"
    assert contributors[0]["login"] == "octocat"
    assert "Python" in languages

    people = real_infoscience.search_person("alice smith")
    labs = real_infoscience.search_orgunit("epfl")
    publications = real_infoscience.search_publications("metadata")

    assert people[0]["infosciencePersonIdentifier"]
    assert labs[0]["infoscienceOrgUnitIdentifier"]
    assert publications[0]["infosciencePublicationIdentifier"] == "123"

    orcid_record = real_orcid.get_person_by_orcid("0000-0002-1825-0097")
    assert orcid_record["name"] == "Alice Example"
    assert orcid_record["employment"]

    ror_organization = real_ror.get_organization("https://ror.org/02s376052")
    ror_matches = real_ror.search_organizations("epfl")
    assert ror_organization["id"] == "https://ror.org/02s376052"
    assert ror_matches


def test_get_provider_factory_returns_expected_mock_and_real_implementations() -> None:
    assert isinstance(get_provider("github", use_mock=True), MockGitHubProvider)
    assert isinstance(get_provider("orcid", use_mock=True), MockORCIDProvider)
    assert isinstance(get_provider("infoscience", use_mock=True), MockInfoscienceProvider)
    assert isinstance(get_provider("ror", use_mock=True), MockRORProvider)

    real_github = get_provider(
        "github",
        use_mock=False,
        gimie_extractor=lambda _url, _format: {},
        user_lookup=lambda _username: {},
        organization_lookup=lambda _org_name: {},
        repository_context_loader=lambda _url: {"git_authors": []},
    )
    assert isinstance(real_github, RealGitHubProvider)

    with pytest.raises(ValueError, match="Unknown provider name"):
        get_provider("unsupported")


def test_mock_and_real_github_providers_are_interchangeable_by_interface() -> None:
    mock_provider: GitHubProvider = MockGitHubProvider()
    real_provider: GitHubProvider = _build_real_github_provider()

    def _extract_name(provider: GitHubProvider) -> str:
        repository = provider.get_repository("octocat/Hello-World")
        return str(repository.get("name", ""))

    assert _extract_name(mock_provider)
    assert _extract_name(real_provider)
