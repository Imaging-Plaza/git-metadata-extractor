"""v1 parsers must rotate across a comma-separated GME_GITHUB_TOKEN pool.

Regression test for issue #56: the v1 parsers used to paste the entire
comma-separated value verbatim into a single `Authorization: token …`
header, so multi-PAT deployments hit the 5 000 GraphQL pts/h ceiling of
one token while the rest sat at 0 used.
"""

from __future__ import annotations

import pytest

from git_metadata_extractor.providers import github_token_pool
from git_metadata_extractor.providers.github_accounts.orgs_parser import GitHubOrganizationsParser
from git_metadata_extractor.providers.github_accounts.users_parser import GitHubUsersParser


class _StubResponse:
    def __init__(self, status_code: int = 200, payload: list | dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else []

    def json(self):
        return self._payload

    def raise_for_status(self) -> None:  # parity with requests.Response
        return None


@pytest.fixture(autouse=True)
def _reset_token_cycle(monkeypatch):
    monkeypatch.setattr(github_token_pool, "_CYCLE", None)
    monkeypatch.setattr(github_token_pool, "_SOURCE", None)
    monkeypatch.delenv("GME_GITHUB_TOKEN_POOL", raising=False)


def _capture_auth_headers(monkeypatch, module) -> list[str]:
    captured: list[str] = []

    def _fake_get(url, headers=None, params=None, timeout=None):
        captured.append((headers or {}).get("Authorization", ""))
        return _StubResponse(status_code=200, payload=[])

    monkeypatch.setattr(module.requests, "get", _fake_get)
    return captured


def test_users_parser_rotates_across_pool(monkeypatch):
    monkeypatch.setenv("GME_GITHUB_TOKEN_POOL", "ghp_a,ghp_b,ghp_c")

    from git_metadata_extractor.providers.github_accounts import users_parser

    captured = _capture_auth_headers(monkeypatch, users_parser)

    parser = GitHubUsersParser()
    # Pick a method that hits requests.get exactly once and ignores the body.
    parser._get_user_organizations("octocat")
    parser._get_user_organizations("octocat")
    parser._get_user_organizations("octocat")
    parser._get_user_organizations("octocat")

    assert captured == [
        "token ghp_a",
        "token ghp_b",
        "token ghp_c",
        "token ghp_a",
    ]


def test_orgs_parser_rotates_across_pool(monkeypatch):
    monkeypatch.setenv("GME_GITHUB_TOKEN_POOL", "ghp_x,ghp_y")

    from git_metadata_extractor.providers.github_accounts import orgs_parser

    captured = _capture_auth_headers(monkeypatch, orgs_parser)

    parser = GitHubOrganizationsParser()
    parser._get_organization_public_members("imaging-plaza")
    parser._get_organization_public_members("imaging-plaza")
    parser._get_organization_public_members("imaging-plaza")

    assert captured == [
        "token ghp_x",
        "token ghp_y",
        "token ghp_x",
    ]


def test_users_parser_graphql_uses_rotated_token(monkeypatch):
    monkeypatch.setenv("GME_GITHUB_TOKEN_POOL", "ghp_g1,ghp_g2")

    from git_metadata_extractor.providers.github_accounts import users_parser

    captured: list[str] = []

    def _fake_post(url, headers=None, data=None, timeout=None):
        captured.append((headers or {}).get("Authorization", ""))
        return _StubResponse(status_code=200, payload={"data": {"user": None}})

    monkeypatch.setattr(users_parser.requests, "post", _fake_post)

    parser = GitHubUsersParser()
    parser._get_graphql_user_data("octocat")
    parser._get_graphql_user_data("octocat")
    parser._get_graphql_user_data("octocat")

    assert captured == ["token ghp_g1", "token ghp_g2", "token ghp_g1"]
