from __future__ import annotations

import pytest

from src.v2.ingest.detection import GitHubURLType, classify_github_url


def test_repository_url_detected_with_owner_and_repo() -> None:
    result = classify_github_url("https://github.com/owner/repo")

    assert result.detected_type == GitHubURLType.REPOSITORY
    assert result.owner == "owner"
    assert result.repo == "repo"
    assert result.normalized_url == "https://github.com/owner/repo"


def test_repository_url_git_suffix_is_stripped() -> None:
    result = classify_github_url("https://github.com/owner/repo.git")

    assert result.detected_type == GitHubURLType.REPOSITORY
    assert result.owner == "owner"
    assert result.repo == "repo"
    assert result.normalized_url == "https://github.com/owner/repo"


def test_user_url_detected_from_single_path_segment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `github.com/<name>` probes the GitHub API; pin the probe so the
    # unit test is deterministic and offline.
    monkeypatch.setattr(
        "src.v2.ingest.detection.github_url_classifier._probe_account_type",
        lambda _name: "User",
    )
    result = classify_github_url("https://github.com/username")

    assert result.detected_type == GitHubURLType.USER
    assert result.owner == "username"
    assert result.repo is None
    assert result.normalized_url == "https://github.com/username"


def test_orgs_url_detected_as_organization() -> None:
    result = classify_github_url("https://github.com/orgs/orgname")

    assert result.detected_type == GitHubURLType.ORGANIZATION
    assert result.owner == "orgname"
    assert result.repo is None
    assert result.normalized_url == "https://github.com/orgs/orgname"


def test_ambiguous_org_or_user_defaults_to_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Probe unavailable (offline / rate-limited / no token) → the
    # classifier falls back to the historical USER default.
    monkeypatch.setattr(
        "src.v2.ingest.detection.github_url_classifier._probe_account_type",
        lambda _name: None,
    )
    result = classify_github_url("https://github.com/orgname")

    assert result.detected_type == GitHubURLType.USER
    assert result.owner == "orgname"
    assert result.repo is None
    assert result.normalized_url == "https://github.com/orgname"


def test_single_segment_probed_as_organization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # When the probe resolves the bare `github.com/<name>` form to an
    # Organization, the classifier reports ORGANIZATION (not USER).
    monkeypatch.setattr(
        "src.v2.ingest.detection.github_url_classifier._probe_account_type",
        lambda _name: "Organization",
    )
    result = classify_github_url("https://github.com/DeepLabCut")

    assert result.detected_type == GitHubURLType.ORGANIZATION
    assert result.owner == "DeepLabCut"
    assert result.repo is None
    assert result.normalized_url == "https://github.com/DeepLabCut"


def test_query_fragment_and_trailing_slash_are_normalized() -> None:
    result = classify_github_url("https://GitHub.com/Owner/Repo.git/?tab=readme#intro")

    assert result.detected_type == GitHubURLType.REPOSITORY
    assert result.owner == "Owner"
    assert result.repo == "Repo"
    assert result.normalized_url == "https://github.com/Owner/Repo"


def test_non_github_url_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Non-GitHub URL not supported"):
        classify_github_url("https://gitlab.com/owner/repo")
