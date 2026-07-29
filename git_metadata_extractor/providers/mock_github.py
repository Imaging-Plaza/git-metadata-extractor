from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Never

from git_metadata_extractor.providers.base import (
    GitHubProvider,
    ProviderNotFoundError,
    ProviderPermissionError,
    ProviderRateLimitError,
)


class MockGitHubProvider(GitHubProvider):
    """Fixture-backed GitHub provider used by v2 tests and local development."""

    def __init__(self, fixture_root: Path | None = None) -> None:
        self._fixture_root = fixture_root or self._default_fixture_root()
        self._repo_payload = self._load_json_fixture("repo_payload")
        self._user_payload = self._load_json_fixture("user_payload")
        self._org_payload = self._load_json_fixture("org_payload")
        self._contributors_payload = self._load_json_fixture("contributors_payload")
        self._rate_limited_payload = self._load_json_fixture("rate_limited_response")
        self._not_found_payload = self._load_json_fixture("not_found_response")

    @staticmethod
    def _default_fixture_root() -> Path:
        return (
            Path(__file__).resolve().parents[2]
            / "tests"
            / "v2"
            / "fixtures"
            / "providers"
            / "github"
        )

    def _load_json_fixture(self, fixture_name: str) -> dict[str, Any]:
        fixture_path = self._fixture_root / f"{fixture_name}.json"
        with fixture_path.open(encoding="utf-8") as fixture_file:
            payload = json.load(fixture_file)

        if not isinstance(payload, dict):
            raise TypeError
        return payload

    def _raise_repo_error(self, full_name: str) -> Never:
        if full_name == "private/repo":
            private_repo_error = self._repo_payload.get("private_repo_error", {})
            message = str(private_repo_error.get("message", "Repository is private"))
            raise ProviderPermissionError(message)
        if full_name == "rate-limited/repo":
            message = str(self._rate_limited_payload.get("message", "Rate limited"))
            raise ProviderRateLimitError(message)

        message = str(self._not_found_payload.get("message", "Not found"))
        raise ProviderNotFoundError(message)

    def get_repository(self, full_name: str) -> dict[str, Any]:
        rest_payload = self._repo_payload.get("rest")
        if full_name == "octocat/Hello-World" and isinstance(rest_payload, dict):
            return deepcopy(rest_payload)

        self._raise_repo_error(full_name)
        raise AssertionError

    def get_user(self, username: str) -> dict[str, Any]:
        if username != "octocat":
            message = str(self._not_found_payload.get("message", "Not found"))
            raise ProviderNotFoundError(message)

        return deepcopy(self._user_payload)

    def get_organization(self, org_name: str) -> dict[str, Any]:
        if org_name != "github":
            message = str(self._not_found_payload.get("message", "Not found"))
            raise ProviderNotFoundError(message)

        return deepcopy(self._org_payload)

    def get_contributors(self, full_name: str) -> list[dict[str, Any]]:
        rest_payload = self._contributors_payload.get("rest")
        if full_name == "octocat/Hello-World" and isinstance(rest_payload, list):
            return deepcopy(rest_payload)

        self._raise_repo_error(full_name)
        raise AssertionError

    def get_languages(self, full_name: str) -> dict[str, int]:
        languages = self._repo_payload.get("languages")
        if full_name == "octocat/Hello-World" and isinstance(languages, dict):
            return deepcopy(languages)

        self._raise_repo_error(full_name)
        raise AssertionError

    def get_repository_sbom(self, full_name: str) -> list[dict[str, Any]] | None:
        if full_name != "octocat/Hello-World":
            return None
        return [
            {
                "name": "requests",
                "ecosystem": "pypi",
                "version": "2.31.0",
                "spdxId": "SPDXRef-pypi-requests",
            },
            {
                "name": "left-pad",
                "ecosystem": "npm",
                "version": "1.3.0",
                "spdxId": "SPDXRef-npm-left-pad",
            },
        ]
