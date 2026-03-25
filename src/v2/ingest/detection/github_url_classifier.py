from __future__ import annotations

import os
from urllib.parse import unquote, urlparse

from src.v2.ingest.detection.models import (
    GitHubURLClassification,
    GitHubURLType,
    UnsupportedGitHubURL,
)

DEFAULT_GITHUB_BASE_URL = "https://github.com"
MIN_ORG_URL_SEGMENTS = 2
MIN_REPO_URL_SEGMENTS = 2

UNSUPPORTED_REPO_PATH_REASONS = {
    "issues": "issue URLs not supported",
    "pull": "pull request URLs not supported",
    "blob": "file URLs not supported",
    "tree": "tree URLs not supported",
    "commit": "commit URLs not supported",
    "commits": "commit URLs not supported",
    "actions": "actions URLs not supported",
    "releases": "releases URLs not supported",
    "wiki": "wiki URLs not supported",
    "settings": "settings URLs not supported",
    "security": "security URLs not supported",
}

EMPTY_URL_ERROR = "GitHub URL cannot be empty"
BASE_PATH_MISMATCH_ERROR = "GitHub URL does not match configured V2_GITHUB_BASE_URL path"
MISSING_PATH_ERROR = "GitHub URL must include a user, organization, or repository path"
MISSING_ORGANIZATION_NAME_ERROR = "Organization URL must include an organization name"
EMPTY_REPOSITORY_NAME_ERROR = "Repository name cannot be empty"
ORGANIZATION_SUBRESOURCE_UNSUPPORTED_REASON = "organization subresource URLs not supported"


def _parse_url(raw_url: str) -> tuple[str, str, list[str]]:
    candidate = raw_url.strip()
    if not candidate:
        raise ValueError(EMPTY_URL_ERROR)
    if "://" not in candidate:
        candidate = f"https://{candidate}"

    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        message = f"Invalid GitHub URL: {raw_url}"
        raise ValueError(message)

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        message = f"Invalid GitHub URL: {raw_url}"
        raise ValueError(message)

    decoded_segments = [unquote(segment) for segment in parsed.path.split("/") if segment]
    return parsed.scheme.lower(), hostname, decoded_segments


def _parse_github_base_url() -> tuple[str, list[str]]:
    configured_base = os.getenv("V2_GITHUB_BASE_URL", DEFAULT_GITHUB_BASE_URL).strip()
    if not configured_base:
        configured_base = DEFAULT_GITHUB_BASE_URL
    if "://" not in configured_base:
        configured_base = f"https://{configured_base}"

    _, hostname, path_segments = _parse_url(configured_base)
    return hostname, path_segments


def _host_matches(configured_host: str, url_host: str) -> bool:
    if configured_host == url_host:
        return True
    return configured_host == "github.com" and url_host == "www.github.com"


def _strip_base_segments(
    path_segments: list[str],
    base_path_segments: list[str],
) -> list[str]:
    if not base_path_segments:
        return path_segments
    if len(path_segments) < len(base_path_segments):
        raise ValueError(BASE_PATH_MISMATCH_ERROR)

    left = [segment.lower() for segment in path_segments[: len(base_path_segments)]]
    right = [segment.lower() for segment in base_path_segments]
    if left != right:
        raise ValueError(BASE_PATH_MISMATCH_ERROR)

    return path_segments[len(base_path_segments) :]


def _build_base_url(hostname: str, base_path_segments: list[str]) -> str:
    if not base_path_segments:
        return f"https://{hostname}"
    return f"https://{hostname}/{'/'.join(base_path_segments)}"


def _strip_repo_suffix(repo_name: str) -> str:
    if repo_name.lower().endswith(".git"):
        return repo_name[:-4]
    return repo_name


def classify_github_url(url: str) -> GitHubURLClassification:
    _, hostname, path_segments = _parse_url(url)
    configured_host, configured_path_segments = _parse_github_base_url()

    if not _host_matches(configured_host, hostname):
        message = f"Non-GitHub URL not supported: {url}"
        raise ValueError(message)

    relative_segments = _strip_base_segments(path_segments, configured_path_segments)
    if not relative_segments:
        raise ValueError(MISSING_PATH_ERROR)

    base_url = _build_base_url(configured_host, configured_path_segments)
    first_segment = relative_segments[0]

    if first_segment.lower() == "orgs":
        if len(relative_segments) < MIN_ORG_URL_SEGMENTS:
            raise ValueError(MISSING_ORGANIZATION_NAME_ERROR)

        organization_name = relative_segments[1]
        normalized_url = f"{base_url}/orgs/{organization_name}"
        if len(relative_segments) > MIN_ORG_URL_SEGMENTS:
            raise UnsupportedGitHubURL(
                ORGANIZATION_SUBRESOURCE_UNSUPPORTED_REASON,
                normalized_url,
            )

        return GitHubURLClassification(
            normalized_url=normalized_url,
            detected_type=GitHubURLType.ORGANIZATION,
            owner=organization_name,
            repo=None,
        )

    owner = first_segment
    if len(relative_segments) == 1:
        return GitHubURLClassification(
            normalized_url=f"{base_url}/{owner}",
            detected_type=GitHubURLType.USER,
            owner=owner,
            repo=None,
        )

    repo = _strip_repo_suffix(relative_segments[1])
    if not repo:
        raise ValueError(EMPTY_REPOSITORY_NAME_ERROR)

    normalized_repository_url = f"{base_url}/{owner}/{repo}"
    if len(relative_segments) > MIN_REPO_URL_SEGMENTS:
        unsupported_segment = relative_segments[MIN_REPO_URL_SEGMENTS].lower()
        reason = UNSUPPORTED_REPO_PATH_REASONS.get(
            unsupported_segment,
            "repository subresource URLs not supported",
        )
        raise UnsupportedGitHubURL(reason, normalized_repository_url)

    return GitHubURLClassification(
        normalized_url=normalized_repository_url,
        detected_type=GitHubURLType.REPOSITORY,
        owner=owner,
        repo=repo,
    )
