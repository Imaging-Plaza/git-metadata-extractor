"""Ranking and a shared subject word must not create institutional ownership."""

import asyncio

import pytest

from git_metadata_extractor.agents.models import ProviderSet
from git_metadata_extractor.agents.rule_based.organization_agent import (
    OrganizationAgentV2,
    _select_ror_match,
)
from git_metadata_extractor.pipeline.stages.ownership_check import _select_ror_parent
from git_metadata_extractor.providers.mock_github import MockGitHubProvider
from git_metadata_extractor.providers.mock_ror import MockRORProvider

FOUNDATION = {
    "id": "https://ror.org/03zh00e46",
    "name": "Normalization Housing Foundation",
    "aliases": [],
    "acronyms": [],
    "labels": [],
    "country": {"country_code": "JP"},
    "links": ["https://normalize.or.jp"],
    "types": ["nonprofit"],
}


@pytest.mark.parametrize("country_bias", [None, "JP"])
def test_single_shared_subject_word_is_insufficient(country_bias):
    assert (
        _select_ror_match(
            [FOUNDATION],
            country_bias=country_bias,
            warnings=[],
            ror_query="Advanced Normalization Tools Ecosystem",
        )
        is None
    )


@pytest.mark.parametrize("homepage", [None, "https://ants.dev"])
def test_parent_selector_cannot_promote_a_subject_word_collision(homepage):
    assert (
        asyncio.run(
            _select_ror_parent(
                handle="ANTsX",
                org_name="Advanced Normalization Tools Ecosystem",
                shortlist=[(3, FOUNDATION.copy())],
                parent_selector=None,
                org_context={"homepage": homepage},
                warnings=[],
            ),
        )
        is None
    )


@pytest.mark.parametrize("ror_url", ["https://acme.org", "https://unrelated.org"])
def test_exact_name_does_not_override_conflicting_web_identity(ror_url):
    record = {**FOUNDATION, "name": "Acme Institute", "links": [ror_url]}
    assert (
        _select_ror_match(
            [record],
            country_bias=None,
            warnings=[],
            ror_query="Acme Institute",
            homepage="https://acme.com",
        )
        is None
    )
    assert (
        asyncio.run(
            _select_ror_parent(
                handle="acme",
                org_name="Acme Institute",
                shortlist=[(3, record)],
                parent_selector=None,
                org_context={"homepage": "https://acme.com"},
                warnings=[],
            ),
        )
        is None
    )


def test_institutional_subdomain_and_exact_alias_still_match():
    record = {
        **FOUNDATION,
        "name": "Example University",
        "aliases": ["Example Research Institute"],
        "links": ["https://www.example.edu"],
    }
    assert (
        _select_ror_match(
            [record],
            country_bias=None,
            warnings=[],
            ror_query="Example Research Institute",
            homepage="https://research.example.edu",
        )
        is record
    )


def test_organization_agent_keeps_github_identity_when_ror_candidate_is_unsupported():
    class GitHub(MockGitHubProvider):
        def get_organization(self, org_name):
            return {
                "login": org_name,
                "name": "Advanced Normalization Tools Ecosystem",
                "blog": "https://ants.dev",
                "location": "United States",
                "description": "Biomedical image analysis",
                "repositories": ["ANTs"],
            }

        def get_profile_readme(self, *_args, **_kwargs):
            return ""

    class ROR(MockRORProvider):
        def search_organizations(self, _query):
            return [FOUNDATION.copy()]

    result = asyncio.run(
        OrganizationAgentV2().run(
            {
                "org_name": "ANTsX",
                "source_repositories": ["https://github.com/ANTsX/ANTs"],
            },
            ProviderSet(github=GitHub(), ror=ROR()),
        ),
    )
    assert result.data["id"] == "https://github.com/ANTsX"
    assert not result.data.get("pulse:ror")
    assert not result.data.get("org:unitOf")


def test_exact_generic_name_requires_supporting_website_identity():
    record = {**FOUNDATION, "name": "Digital Science", "links": ["https://www.digital-science.com"]}
    assert _select_ror_match(
        [record], country_bias=None, warnings=[], ror_query="Digital Science",
        homepage="https://digital-science.com",
    ) is record
    assert _select_ror_match(
        [record], country_bias=None, warnings=[], ror_query="Digital Science",
    ) is None
