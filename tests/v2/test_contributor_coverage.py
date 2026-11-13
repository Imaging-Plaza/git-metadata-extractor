"""Contributor retrieval must distinguish a bounded result from unavailable data."""

# ruff: noqa: PLR2004, EM101, TRY003, SIM117
from __future__ import annotations

import asyncio
from typing import Any

import pytest
import requests

from git_metadata_extractor.agents import ProviderSet
from git_metadata_extractor.observation.evidence import activity, observe_provider
from git_metadata_extractor.observation.query_log import QueryLog, query_log_var
from git_metadata_extractor.observation.run_report import observe_stage, report_scope
from git_metadata_extractor.pipeline.stages.context_gather import gather_context
from git_metadata_extractor.providers.base import ProviderError
from git_metadata_extractor.providers.detection.models import (
    GitHubURLClassification,
    GitHubURLType,
)
from git_metadata_extractor.providers.gimie_api_client import extract_gimie_via_api
from git_metadata_extractor.providers.github_provider import RealGitHubProvider


class Response:
    def __init__(self, status: int, body: Any = None, *, more: bool = False):
        self.status_code = status
        self.body = body
        self.headers = (
            {
                "Link": '<https://api.github.com/repos/owner/repo/contributors?page=2>; rel="next"',
            }
            if more
            else {}
        )

    def json(self):
        return self.body


def install_http(monkeypatch, responses):
    calls = []

    def get(url, *, params, **kwargs):
        assert url == "https://api.github.com/repos/owner/repo/contributors"
        assert params["per_page"] == 100
        assert params["page"] == len(calls) + 1
        assert kwargs["timeout"] <= 20
        calls.append(params)
        response = responses[len(calls) - 1]
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(
        "git_metadata_extractor.providers.github_provider.requests.get", get,
    )
    return calls


def provider(payload=None):
    return RealGitHubProvider(gimie_extractor=lambda _url, _fmt: payload)


def test_missing_gimie_contributors_falls_back_to_rest_with_real_counts(monkeypatch):
    calls = install_http(
        monkeypatch,
        [Response(200, [{"login": "alice", "contributions": 37, "type": "User"}])],
    )
    result = provider().get_contributors_with_coverage("owner/repo")
    assert result.contributors == [
        {"login": "alice", "name": None, "email": None, "contributions": 37},
    ]
    assert result.coverage.status == "complete"
    assert result.coverage.source == "github_rest"
    assert result.coverage.fallback_reason == "gimie_no_contributor_identities"
    assert result.fetched is True
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (
            Response(
                422,
                {
                    "message": "The history or contributor list is too large to list contributors for this repository via the API.",
                },
            ),
            "github_http_422",
        ),
        (Response(403), "github_http_403"),
        (requests.ConnectionError("offline"), "github_request_failed"),
        (Response(200, {"message": "bad payload"}), "github_invalid_response"),
    ],
)
def test_fallback_failures_are_unavailable_not_empty_success(
    monkeypatch, response, reason,
):
    calls = install_http(monkeypatch, [response])
    result = provider().get_contributors_with_coverage("owner/repo")
    assert result.contributors == []
    assert result.coverage.status == "unavailable"
    assert result.coverage.reason == reason
    assert result.fetched is False
    assert len(calls) == 1


@pytest.mark.parametrize("response", [Response(204), Response(200, [])])
def test_true_empty_repository_is_successful_empty_coverage(monkeypatch, response):
    calls = install_http(monkeypatch, [response])
    result = provider().get_contributors_with_coverage("owner/repo")
    assert result.contributors == []
    assert result.coverage.status == "empty"
    assert result.fetched is True
    assert len(calls) == 1


def test_legacy_list_interface_does_not_hide_unavailable_lookup(monkeypatch):
    install_http(monkeypatch, [Response(422)])
    with pytest.raises(ProviderError, match="unavailable"):
        provider().get_contributors("owner/repo")


def test_pagination_stops_at_cap_and_records_partial_coverage(monkeypatch):
    pages = [
        Response(
            200,
            [
                {"login": f"person{page}-{i}", "contributions": i + 1}
                for i in range(100)
            ],
            more=True,
        )
        for page in range(3)
    ]
    calls = install_http(monkeypatch, pages)
    result = provider().get_contributors_with_coverage("owner/repo")
    assert len(calls) == 3
    assert len(result.contributors) == 300
    assert result.coverage.status == "partial"
    assert result.coverage.reason == "page_limit"
    assert result.coverage.pages_fetched == 3


def test_second_page_failure_preserves_first_page_without_claiming_complete(
    monkeypatch,
):
    calls = install_http(
        monkeypatch,
        [
            Response(200, [{"login": "alice", "contributions": 2}], more=True),
            Response(502),
        ],
    )
    result = provider().get_contributors_with_coverage("owner/repo")
    assert result.contributors[0]["contributions"] == 2
    assert result.coverage.status == "partial"
    assert result.coverage.reason == "github_http_502"
    assert result.coverage.pages_fetched == 1
    assert len(calls) == 2


def test_completed_pagination_deduplicates_identities_and_does_not_invent_counts(
    monkeypatch,
):
    install_http(
        monkeypatch,
        [
            Response(200, [{"login": "alice", "contributions": 2}], more=True),
            Response(200, [{"login": "alice", "contributions": 2}, {"login": "bob"}]),
        ],
    )
    result = provider().get_contributors_with_coverage("owner/repo")
    assert [p["login"] for p in result.contributors] == ["alice", "bob"]
    assert result.contributors[1]["contributions"] is None
    assert result.coverage.status == "complete"


def test_nonempty_gimie_result_preserves_unknown_enumeration_coverage(monkeypatch):
    def no_network(*_args, **_kwargs):
        raise AssertionError("No fallback needed")

    monkeypatch.setattr(
        "git_metadata_extractor.providers.github_provider.requests.get", no_network,
    )
    result = provider(
        {"schema:contributor": [{"@id": "https://github.com/alice"}]},
    ).get_contributors_with_coverage("owner/repo")
    assert result.contributors[0]["login"] == "alice"
    assert result.coverage.status == "unknown"
    assert result.coverage.source == "gimie"
    assert result.contributors[0]["contributions"] is None


@pytest.mark.parametrize(
    ("response", "expected_status"),
    [
        (Response(422), "unavailable"),
        (Response(200, [{"login": "alice"}], more=True), "partial"),
    ],
)
def test_context_exposes_degraded_contributors_in_warnings_and_report(
    monkeypatch, response, expected_status,
):
    github = provider()
    # Isolate all unrelated retrieval at its provider boundary; retain real contributor fallback.
    monkeypatch.setattr(
        github, "get_repository", lambda _name: {"full_name": "owner/repo"},
    )
    monkeypatch.setattr(github, "get_languages", lambda _name: {})
    for name, value in [
        ("get_repository_readme", "README"),
        ("get_repository_jsonld", {}),
        ("get_repository_aux_files", {}),
        ("get_repository_releases", []),
        ("get_repository_container_images", []),
        ("get_repository_community_profile", {}),
        ("get_repository_tags", []),
        ("get_repository_compose_files", []),
        ("get_repository_root_entries", []),
    ]:
        monkeypatch.setattr(github, name, lambda _name, value=value: value)
    monkeypatch.setenv("V2_CONTRIBUTOR_BOOKENDS_TOP_N", "0")
    install_http(monkeypatch, [response] * 3)
    info = GitHubURLClassification(
        normalized_url="https://github.com/owner/repo",
        detected_type=GitHubURLType.REPOSITORY,
        owner="owner",
        repo="repo",
    )
    with report_scope(enabled=True):
        with observe_stage("context_gather", phase="generation") as stage:
            result = asyncio.run(
                gather_context("repository", info, ProviderSet(github=github)),
            )
    assert any(
        "Contributor coverage" in warning and expected_status in warning
        for warning in result.warnings
    )
    assert (
        result.context["repository"]["contributor_coverage"]["status"]
        == expected_status
    )
    assert stage.status == "partial"
    assert stage.reason == f"contributors_{expected_status}"


def test_sidecar_error_contract_uses_bounded_fallback(monkeypatch):

    class FailedSidecar:
        def get(self, _url, **_kwargs):
            return Response(
                200,
                {
                    "output": "API request failed: The history or contributor list is too large to list contributors for this repository via the API.",
                },
            )

    monkeypatch.setenv("GIMIE_API_URL", "http://sidecar.invalid")
    calls = install_http(
        monkeypatch, [Response(200, [{"login": "alice", "contributions": 8}])],
    )
    github = RealGitHubProvider(
        gimie_extractor=lambda url, fmt: extract_gimie_via_api(
            url, fmt, session=FailedSidecar(),
        ),
    )
    result = github.get_contributors_with_coverage("owner/repo")
    assert result.contributors[0]["contributions"] == 8
    assert result.coverage.status == "complete"
    assert len(calls) == 1


def test_unavailable_coverage_is_recorded_as_failed_retrieval(monkeypatch):

    install_http(monkeypatch, [Response(422)])
    log = QueryLog("coverage-test", "https://github.com/owner/repo")
    token = query_log_var.set(log)
    try:
        with activity("context_gather", kind="retrieval"):
            result = observe_provider(
                provider(), "github",
            ).get_contributors_with_coverage("owner/repo")
        assert result.coverage.status == "unavailable"
        queries = list(log.evidence.queries.values())
        assert len(queries) == 1
        assert queries[0]["status"] == "failed"
    finally:
        query_log_var.reset(token)


def test_unavailable_fallback_is_not_cached_as_empty_success(monkeypatch):
    github = provider()
    install_http(monkeypatch, [Response(422)])
    assert github.get_contributors_with_coverage("owner/repo").fetched is False
    install_http(monkeypatch, [Response(200, [{"login": "alice", "contributions": 9}])])
    retry = github.get_contributors_with_coverage("owner/repo")
    assert retry.coverage.status == "complete"
    assert retry.contributors[0]["contributions"] == 9


def test_gimie_exception_also_allows_rest_recovery(monkeypatch):
    def broken_gimie(_url, _fmt):
        raise RuntimeError("sidecar is unavailable")

    install_http(monkeypatch, [Response(200, [{"login": "alice"}])])
    github = RealGitHubProvider(gimie_extractor=broken_gimie)
    result = github.get_contributors_with_coverage("owner/repo")
    assert result.coverage.fallback_reason == "gimie_lookup_failed"
    assert result.coverage.status == "complete"
