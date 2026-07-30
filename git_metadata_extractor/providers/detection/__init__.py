"""GitHub URL detection utilities for the v2 extraction pipeline."""

from git_metadata_extractor.providers.detection.github_url_classifier import classify_github_url
from git_metadata_extractor.providers.detection.models import (
    GitHubURLClassification,
    GitHubURLType,
    UnsupportedGitHubURL,
)

__all__ = [
    "GitHubURLClassification",
    "GitHubURLType",
    "UnsupportedGitHubURL",
    "classify_github_url",
]
