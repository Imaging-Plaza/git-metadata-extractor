from __future__ import annotations

import pytest

from src.v2.ingest.detection import GitHubURLType, UnsupportedGitHubURL, classify_github_url

UNSUPPORTED_URL_CASES = [
    ("https://github.com/owner/repo/issues/123", "issue URLs not supported"),
    ("https://github.com/owner/repo/pull/456", "pull request URLs not supported"),
    ("https://github.com/owner/repo/blob/main/file.py", "file URLs not supported"),
    ("https://github.com/owner/repo/tree/main", "tree URLs not supported"),
    ("https://github.com/owner/repo/commit/abc123", "commit URLs not supported"),
    ("https://github.com/owner/repo/actions", "actions URLs not supported"),
    ("https://github.com/owner/repo/releases", "releases URLs not supported"),
    ("https://github.com/owner/repo/wiki", "wiki URLs not supported"),
    ("https://github.com/owner/repo/settings", "settings URLs not supported"),
    ("https://github.com/owner/repo/security", "security URLs not supported"),
]


@pytest.mark.parametrize(("url", "expected_reason"), UNSUPPORTED_URL_CASES)
def test_unsupported_repository_subresource_urls_raise_typed_error(
    url: str,
    expected_reason: str,
) -> None:
    with pytest.raises(UnsupportedGitHubURL) as error:
        classify_github_url(url)

    assert error.value.reason == expected_reason


def test_http_url_is_upgraded_to_https() -> None:
    result = classify_github_url("http://github.com/owner/repo")

    assert result.detected_type == GitHubURLType.REPOSITORY
    assert result.normalized_url == "https://github.com/owner/repo"


def test_url_encoded_owner_and_repo_are_decoded() -> None:
    result = classify_github_url("https://github.com/open%2Dpulse/repo%2Dname")

    assert result.detected_type == GitHubURLType.REPOSITORY
    assert result.owner == "open-pulse"
    assert result.repo == "repo-name"
    assert result.normalized_url == "https://github.com/open-pulse/repo-name"


def test_enterprise_base_url_is_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V2_GITHUB_BASE_URL", "https://github.enterprise.local/scm")

    result = classify_github_url(
        "http://github.enterprise.local/scm/owner/repo.git?utm_source=test#readme",
    )

    assert result.detected_type == GitHubURLType.REPOSITORY
    assert result.owner == "owner"
    assert result.repo == "repo"
    assert result.normalized_url == "https://github.enterprise.local/scm/owner/repo"
