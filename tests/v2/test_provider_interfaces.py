from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from git_metadata_extractor.providers.infoscience_models import InfoscienceAuthor, InfoscienceSearchResult
from git_metadata_extractor.providers.github_accounts.orgs_parser import GitHubOrganizationsParser
from git_metadata_extractor.providers.github_accounts.users_parser import GitHubUsersParser
from git_metadata_extractor.providers import (
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
from git_metadata_extractor.providers.base import (
    INFOSCIENCE_PUBLICATION_OPTIONAL_FIELDS,
    INFOSCIENCE_PUBLICATION_REQUIRED_FIELDS,
)
from git_metadata_extractor.providers.github_provider import RealGitHubProvider
from git_metadata_extractor.providers.infoscience_provider import RealInfoscienceProvider
from git_metadata_extractor.providers.orcid_provider import RealORCIDProvider
from git_metadata_extractor.providers.ror_provider import RealRORProvider

STATUS_ERROR_THRESHOLD = 400


class _SpyUsersParser(GitHubUsersParser):
    def __init__(self) -> None:
        self.repo_calls = 0

    def _get_rest_user_data(self, username: str) -> dict[str, Any]:
        return {
            "login": username,
            "name": "Alice Smith",
            "bio": None,
            "email": None,
            "location": None,
            "company": None,
            "blog": None,
            "twitter_username": None,
            "public_repos": 0,
            "public_gists": 0,
            "followers": 0,
            "following": 0,
            "created_at": "2020-01-01T00:00:00Z",
            "updated_at": "2020-01-01T00:00:00Z",
            "avatar_url": "https://example.org/avatar.png",
            "html_url": f"https://github.com/{username}",
        }

    def _get_graphql_user_data(self, username: str) -> dict[str, Any]:
        del username
        return {"social_accounts": []}

    def _get_user_organizations(self, username: str) -> list[str]:
        del username
        return []

    def _get_user_readme(self, username: str) -> dict[str, Any]:
        del username
        return {"url": None, "content": None}

    def _scrape_orcid_from_profile(self, username: str) -> str | None:
        del username
        return None

    def _scrape_orcid_activities(self, orcid_id: str) -> Any:
        del orcid_id
        return None

    def _get_user_repositories(self, username: str, limit: int = 100) -> list[str]:
        del username, limit
        self.repo_calls += 1
        return ["repo-a"]


class _SpyOrganizationsParser(GitHubOrganizationsParser):
    def __init__(self) -> None:
        self.repo_calls = 0

    def _get_rest_organization_data(self, org_name: str) -> dict[str, Any]:
        return {
            "login": org_name,
            "name": "Org",
            "description": None,
            "email": None,
            "location": None,
            "company": None,
            "blog": None,
            "twitter_username": None,
            "public_repos": 0,
            "public_gists": 0,
            "followers": 0,
            "following": 0,
            "created_at": "2020-01-01T00:00:00Z",
            "updated_at": "2020-01-01T00:00:00Z",
            "avatar_url": "https://example.org/avatar.png",
            "html_url": f"https://github.com/{org_name}",
            "gravatar_id": "",
            "type": "Organization",
            "node_id": "ORG_1",
            "url": f"https://api.github.com/orgs/{org_name}",
            "repos_url": f"https://api.github.com/orgs/{org_name}/repos",
            "events_url": f"https://api.github.com/orgs/{org_name}/events",
            "hooks_url": f"https://api.github.com/orgs/{org_name}/hooks",
            "issues_url": f"https://api.github.com/orgs/{org_name}/issues",
            "members_url": f"https://api.github.com/orgs/{org_name}/members",
        }

    def _get_graphql_organization_data(self, org_name: str) -> dict[str, Any]:
        del org_name
        return {"social_accounts": [], "pinned_repositories": []}

    def _get_organization_public_members(self, org_name: str) -> list[str]:
        del org_name
        return []

    def _get_organization_repositories(
        self,
        org_name: str,
        limit: int = 100,
    ) -> list[str]:
        del org_name, limit
        self.repo_calls += 1
        return ["repo-a"]

    def _get_organization_teams(self, org_name: str) -> list[str]:
        del org_name
        return []

    def _get_organization_readme(self, org_name: str) -> dict[str, Any]:
        del org_name
        return {"url": None, "content": None}



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


def _build_real_github_provider(**provider_kwargs: Any) -> RealGitHubProvider:
    return RealGitHubProvider(
        gimie_extractor=lambda _url, _format: {
            "@graph": [
                {
                    "@type": "schema:SoftwareSourceCode",
                    "schema:name": "Hello-World",
                    "schema:codeRepository": "https://github.com/octocat/Hello-World",
                    "schema:programmingLanguage": ["Python"],
                    "schema:contributor": [{"@id": "https://github.com/octocat"}],
                },
                {
                    "@id": "https://github.com/octocat",
                    "@type": ["http://schema.org/Person"],
                    "http://schema.org/name": [{"@value": "The Octocat"}],
                },
            ],
        },
        user_lookup=lambda username: {"login": username, "name": "The Octocat"},
        organization_lookup=lambda org_name: {"login": org_name, "name": "GitHub"},
        **provider_kwargs,
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
                    "authors": ["Alice Example", "Bob Example"],
                    "publication_date": "2025-01-15",
                    "lab": "EPFL ENAC",
                    "url": "https://infoscience.epfl.ch/entities/publication/123",
                },
                {
                    "uuid": "456",
                    "title": "Metadata Without Extras",
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
    assert isinstance(ror_organization.get("acronyms"), list)
    assert isinstance(ror_organization.get("labels"), list)
    assert ror_matches


def test_real_infoscience_person_profile_url_is_coerced_to_string() -> None:
    profile_url = (
        "https://infoscience.epfl.ch/entities/person/"
        "1f0b2b90-9e33-4f9a-9b14-8468f89f2e4d"
    )

    async def _search_authors(_query: str, _max_results: int) -> InfoscienceSearchResult:
        return InfoscienceSearchResult(
            total_results=1,
            authors=[
                InfoscienceAuthor(
                    uuid="1f0b2b90-9e33-4f9a-9b14-8468f89f2e4d",
                    name="Alice Smith",
                    orcid="0000-0002-1825-0097",
                    affiliation="EPFL",
                    profile_url=profile_url,
                ),
            ],
        )

    provider = RealInfoscienceProvider(search_authors_func=_search_authors)
    people = provider.search_person("alice smith")

    assert len(people) == 1
    assert people[0]["profileUrl"] == profile_url
    assert isinstance(people[0]["profileUrl"], str)


def _assert_publication_contract(publication: dict[str, Any]) -> None:
    for field_name in INFOSCIENCE_PUBLICATION_REQUIRED_FIELDS:
        assert field_name in publication

    for field_name in INFOSCIENCE_PUBLICATION_OPTIONAL_FIELDS:
        if field_name in publication:
            assert publication[field_name] is None or isinstance(publication[field_name], str)

    assert isinstance(publication["authors"], list)
    assert publication["authors"] == [
        name
        for name in publication["authors"]
        if isinstance(name, str) and name
    ]
    assert publication["publicationDate"] is None or isinstance(
        publication["publicationDate"],
        str,
    )
    assert publication["doi"] is None or isinstance(publication["doi"], str)
    assert publication["url"] is None or isinstance(publication["url"], str)
    assert publication["title"] is None or isinstance(publication["title"], str)
    assert publication["infosciencePublicationIdentifier"] is None or isinstance(
        publication["infosciencePublicationIdentifier"],
        str,
    )


def test_infoscience_publication_contract_is_enforced_for_real_and_mock_providers() -> None:
    real_infoscience = _build_real_infoscience_provider()
    mock_infoscience = MockInfoscienceProvider()

    real_publications = real_infoscience.search_publications("metadata")
    mock_publications = mock_infoscience.search_publications("geodata")

    assert real_publications
    assert mock_publications

    for publication in [*real_publications, *mock_publications]:
        _assert_publication_contract(publication)

    assert real_publications[0]["authors"] == ["Alice Example", "Bob Example"]
    assert real_publications[0]["publicationDate"] == "2025-01-15"
    assert real_publications[0]["sourceOrganization"] == "EPFL ENAC"

    # Missing upstream fields are normalized to contract defaults.
    assert real_publications[1]["authors"] == []
    assert real_publications[1]["publicationDate"] is None
    assert real_publications[1]["doi"] is None
    assert real_publications[1]["url"] is None


def test_real_github_provider_reads_contributors_from_gimie_payload() -> None:
    provider = RealGitHubProvider(
        gimie_extractor=lambda _url, _format: [
            {
                "@id": "https://github.com/octocat/Hello-World",
                "@type": ["http://schema.org/SoftwareSourceCode"],
                "http://schema.org/contributor": [
                    {"@id": "https://github.com/octocat"},
                    {"@id": "https://github.com/hubot"},
                ],
                "http://schema.org/author": [
                    {"@id": "https://orcid.org/0000-0002-1825-0097"},
                    {"@id": "https://github.com/octocat"},
                ],
            },
        ],
        user_lookup=lambda username: {"login": username},
        organization_lookup=lambda org_name: {"login": org_name},
    )

    contributors = provider.get_contributors("octocat/Hello-World")

    assert [contributor["login"] for contributor in contributors] == [
        "octocat",
        "hubot",
    ]


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


def test_user_parser_skips_repo_endpoint_when_repositories_are_disabled() -> None:
    parser = _SpyUsersParser()

    metadata = parser.get_user_metadata("octocat", include_repositories=False)

    assert metadata.repositories == []
    assert parser.repo_calls == 0


def test_org_parser_skips_repo_endpoint_when_repositories_are_disabled() -> None:
    parser = _SpyOrganizationsParser()

    metadata = parser.get_organization_metadata(
        "github",
        include_repositories=False,
    )

    assert metadata.repositories == []
    assert parser.repo_calls == 0


def test_user_parser_passes_include_repositories_to_metadata_call() -> None:
    parser = _SpyUsersParser()

    metadata = parser.get_user_metadata("octocat", include_repositories=True)

    assert metadata.login == "octocat"
    assert parser.repo_calls == 1


def test_org_parser_passes_include_repositories_to_metadata_call() -> None:
    parser = _SpyOrganizationsParser()

    metadata = parser.get_organization_metadata("github", include_repositories=True)

    assert metadata.login == "github"
    assert parser.repo_calls == 1
