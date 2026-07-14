from __future__ import annotations

from typing import Any

from git_metadata_extractor.agents.llm.agent_tools.github_organization import (
    make_github_organization_metadata_tool,
)


class _FakeGitHubProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_organization(self, org_name: str) -> dict[str, Any]:
        self.calls.append(org_name)
        if org_name == "missing":
            raise ValueError("Organization 'missing' not found")
        return {
            "login": org_name,
            "name": "Swiss Data Science Center" if org_name == "sdsc-ordes" else org_name.title(),
            "description": "description",
            "html_url": f"https://github.com/{org_name}",
            "type": "Organization",
            "followers": 24,
            "public_repos": 7,
            "location": "Lausanne, Switzerland",
            "blog": "https://example.org",
            "email": None,
            "company": "EPFL",
            "created_at": "2020-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "public_members": ["alice", "bob", 5],
            "repositories": ["gimie", "other", None],
            "teams": ["platform"],
        }


def test_get_github_organization_metadata_normalizes_url_and_shapes_payload() -> None:
    provider = _FakeGitHubProvider()
    tool = make_github_organization_metadata_tool(provider)

    payload = tool.function("https://github.com/sdsc-ordes")

    assert provider.calls == ["sdsc-ordes"]
    assert payload["normalized_org_name"] == "sdsc-ordes"
    assert payload["organization"]["login"] == "sdsc-ordes"
    assert payload["organization"]["name"] == "Swiss Data Science Center"
    assert payload["organization"]["html_url"] == "https://github.com/sdsc-ordes"
    assert payload["organization"]["public_members"] == ["alice", "bob"]
    assert payload["organization"]["repositories"] == ["gimie", "other"]
    assert payload["organization"]["teams"] == ["platform"]


def test_get_github_organization_metadata_normalizes_owner_repo_and_at_handle() -> None:
    provider = _FakeGitHubProvider()
    tool = make_github_organization_metadata_tool(provider)

    payload_from_repo = tool.function("sdsc-ordes/gimie")
    payload_from_handle = tool.function("@sdsc-ordes")

    assert provider.calls == ["sdsc-ordes", "sdsc-ordes"]
    assert payload_from_repo["normalized_org_name"] == "sdsc-ordes"
    assert payload_from_handle["normalized_org_name"] == "sdsc-ordes"


def test_get_github_organization_metadata_returns_error_for_empty_name() -> None:
    provider = _FakeGitHubProvider()
    tool = make_github_organization_metadata_tool(provider)

    payload = tool.function("   ")

    assert provider.calls == []
    assert payload["normalized_org_name"] == ""
    assert payload["organization"] is None
    assert payload["error"] == "empty_org_name"


def test_get_github_organization_metadata_returns_error_payload_on_lookup_failure() -> None:
    provider = _FakeGitHubProvider()
    tool = make_github_organization_metadata_tool(provider)

    payload = tool.function("missing")

    assert provider.calls == ["missing"]
    assert payload["normalized_org_name"] == "missing"
    assert payload["organization"] is None
    assert "not found" in payload["error"]
