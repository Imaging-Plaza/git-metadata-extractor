from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from git_metadata_extractor.providers.github_provider import (
    RealGitHubProvider,
    _normalize_sbom,
    _parse_purl,
)


class _FakeResponse:
    def __init__(self, *, status_code: int, payload: Any = None, raise_json: bool = False) -> None:
        self.status_code = status_code
        self._payload = payload
        self._raise_json = raise_json

    def json(self) -> Any:
        if self._raise_json:
            raise ValueError("not json")
        return self._payload


def _build_provider() -> RealGitHubProvider:
    return RealGitHubProvider(
        gimie_extractor=lambda _url, _fmt: {},
        user_lookup=lambda username: {"login": username},
        organization_lookup=lambda org_name: {"login": org_name},
    )


# ---------- _parse_purl ----------


@pytest.mark.parametrize(
    ("purl", "expected"),
    [
        (
            "pkg:pypi/requests@2.31.0",
            {"ecosystem": "pypi", "name": "requests", "version": "2.31.0"},
        ),
        (
            "pkg:npm/@scope/name@1.0.0",
            {"ecosystem": "npm", "name": "@scope/name", "version": "1.0.0"},
        ),
        (
            "pkg:maven/org.apache/commons-lang@2.6",
            {"ecosystem": "maven", "name": "org.apache/commons-lang", "version": "2.6"},
        ),
        (
            "pkg:cargo/serde",
            {"ecosystem": "cargo", "name": "serde", "version": None},
        ),
        (
            "pkg:githubactions/actions/checkout@v4?ref=main",
            {"ecosystem": "githubactions", "name": "actions/checkout", "version": "v4"},
        ),
    ],
)
def test_parse_purl_extracts_ecosystem_name_and_version(
    purl: str,
    expected: dict[str, str | None],
) -> None:
    assert _parse_purl(purl) == expected


@pytest.mark.parametrize(
    "invalid",
    [
        "",
        "not-a-purl",
        "pkg:",
        "pkg:pypi",
        123,
        None,
    ],
)
def test_parse_purl_returns_none_for_invalid_input(invalid: Any) -> None:
    assert _parse_purl(invalid) is None  # type: ignore[arg-type]


# ---------- _normalize_sbom ----------


def test_normalize_sbom_extracts_packages_with_purl_refs() -> None:
    spdx_payload = {
        "sbom": {
            "spdxVersion": "SPDX-2.3",
            "packages": [
                {
                    "SPDXID": "SPDXRef-Repo-octocat-Hello-World",
                    "name": "octocat/Hello-World",
                    "externalRefs": [],
                },
                {
                    "SPDXID": "SPDXRef-pypi-requests",
                    "name": "requests",
                    "versionInfo": "2.31.0",
                    "externalRefs": [
                        {
                            "referenceCategory": "PACKAGE-MANAGER",
                            "referenceType": "purl",
                            "referenceLocator": "pkg:pypi/requests@2.31.0",
                        },
                    ],
                },
                {
                    "SPDXID": "SPDXRef-npm-left-pad",
                    "name": "left-pad",
                    "externalRefs": [
                        {
                            "referenceType": "purl",
                            "referenceLocator": "pkg:npm/left-pad@1.3.0",
                        },
                    ],
                },
            ],
        },
    }

    result = _normalize_sbom(spdx_payload)

    assert result == [
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


def test_normalize_sbom_falls_back_to_version_info_when_purl_omits_version() -> None:
    spdx_payload = {
        "sbom": {
            "packages": [
                {
                    "SPDXID": "SPDXRef-pypi-numpy",
                    "name": "numpy",
                    "versionInfo": "1.26.0",
                    "externalRefs": [
                        {"referenceType": "purl", "referenceLocator": "pkg:pypi/numpy"},
                    ],
                },
            ],
        },
    }

    result = _normalize_sbom(spdx_payload)

    assert result == [
        {
            "name": "numpy",
            "ecosystem": "pypi",
            "version": "1.26.0",
            "spdxId": "SPDXRef-pypi-numpy",
        },
    ]


def test_normalize_sbom_returns_empty_for_unexpected_shapes() -> None:
    assert _normalize_sbom(None) == []
    assert _normalize_sbom({}) == []
    assert _normalize_sbom({"sbom": "not-a-dict"}) == []
    assert _normalize_sbom({"sbom": {"packages": "not-a-list"}}) == []


def test_normalize_sbom_accepts_unwrapped_payload() -> None:
    spdx_payload = {
        "packages": [
            {
                "SPDXID": "SPDXRef-pypi-flask",
                "externalRefs": [
                    {"referenceType": "purl", "referenceLocator": "pkg:pypi/flask@3.0.0"},
                ],
            },
        ],
    }

    result = _normalize_sbom(spdx_payload)

    assert result == [
        {
            "name": "flask",
            "ecosystem": "pypi",
            "version": "3.0.0",
            "spdxId": "SPDXRef-pypi-flask",
        },
    ]


# ---------- RealGitHubProvider.get_repository_sbom ----------


def test_get_repository_sbom_returns_normalized_list_on_200() -> None:
    provider = _build_provider()
    payload = {
        "sbom": {
            "packages": [
                {
                    "SPDXID": "SPDXRef-pypi-requests",
                    "externalRefs": [
                        {
                            "referenceType": "purl",
                            "referenceLocator": "pkg:pypi/requests@2.31.0",
                        },
                    ],
                },
            ],
        },
    }
    response = _FakeResponse(status_code=200, payload=payload)

    with patch(
        "git_metadata_extractor.providers.github_provider.requests.get",
        return_value=response,
    ) as fake_get:
        result = provider.get_repository_sbom("octocat/Hello-World")

    assert result == [
        {
            "name": "requests",
            "ecosystem": "pypi",
            "version": "2.31.0",
            "spdxId": "SPDXRef-pypi-requests",
        },
    ]
    assert fake_get.call_count == 1
    args, _kwargs = fake_get.call_args
    assert args[0].endswith("/repos/octocat/Hello-World/dependency-graph/sbom")


@pytest.mark.parametrize("status_code", [404, 403])
def test_get_repository_sbom_returns_none_on_missing_or_forbidden(status_code: int) -> None:
    provider = _build_provider()
    response = _FakeResponse(status_code=status_code, payload={"message": "nope"})

    with patch(
        "git_metadata_extractor.providers.github_provider.requests.get",
        return_value=response,
    ):
        assert provider.get_repository_sbom("ghost/repo") is None


def test_get_repository_sbom_returns_none_on_other_http_error() -> None:
    provider = _build_provider()
    response = _FakeResponse(status_code=500, payload={"message": "boom"})

    with patch(
        "git_metadata_extractor.providers.github_provider.requests.get",
        return_value=response,
    ):
        assert provider.get_repository_sbom("octocat/Hello-World") is None


def test_get_repository_sbom_returns_none_when_response_not_json() -> None:
    provider = _build_provider()
    response = _FakeResponse(status_code=200, raise_json=True)

    with patch(
        "git_metadata_extractor.providers.github_provider.requests.get",
        return_value=response,
    ):
        assert provider.get_repository_sbom("octocat/Hello-World") is None


def test_get_repository_sbom_returns_none_when_request_raises() -> None:
    provider = _build_provider()

    with patch(
        "git_metadata_extractor.providers.github_provider.requests.get",
        side_effect=ConnectionError("boom"),
    ):
        assert provider.get_repository_sbom("octocat/Hello-World") is None
