"""Tests for NuGet package discovery (badge-driven, Source-Link verified).

No real network — a fake session / fake provider is injected.
"""
from __future__ import annotations

import asyncio
from typing import Any

from git_metadata_extractor.agents import ProviderSet, RepositoryAgentV2
from git_metadata_extractor.agents.rule_based._repo_signals import (
    extract_registry_coords,
    parse_badges,
)
from git_metadata_extractor.providers.mock_github import MockGitHubProvider
from git_metadata_extractor.providers.package_registry_provider import PackageRegistryProvider
from git_metadata_extractor.pipeline.stages.context_gather import (
    _enrich_repository_metadata_with_registry_packages,
)

_EXPECTED_NUGET_VERSIONS = 3


class _FakeResp:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class _NuGetSession:
    """Routes by URL substring: search / flat-container / registration."""

    def __init__(self, *, search: Any, versions: Any, registration: Any) -> None:
        self._search = search
        self._versions = versions
        self._registration = registration
        self.requested: list[str] = []

    def get(self, url: str, timeout: float | None = None, headers: Any = None) -> _FakeResp:  # noqa: ARG002
        self.requested.append(url)
        if "azuresearch" in url:
            return _FakeResp(200, self._search)
        if "v3-flatcontainer" in url:
            return _FakeResp(200, self._versions)
        if "registration" in url:
            return _FakeResp(200, self._registration)
        return _FakeResp(404, None)


def _nuget_session(*, repository: Any = None) -> _NuGetSession:
    catalog = {
        "version": "13.0.4",
        "published": "2025-09-16T08:13:09.313+00:00",
        "projectUrl": "https://www.newtonsoft.com/json",
    }
    if repository is not None:
        catalog["repository"] = repository
    return _NuGetSession(
        search={"data": [{"id": "Newtonsoft.Json", "version": "13.0.4",
                          "projectUrl": "https://www.newtonsoft.com/json"}]},
        versions={"versions": ["11.0.1", "12.0.3", "13.0.4"]},
        registration={"items": [{"items": [{"catalogEntry": catalog}]}]},
    )


class _FakeNuGetProvider:
    def __init__(self, result: dict[str, Any] | None) -> None:
        self._result = result
        self.calls: list[str] = []

    def get_nuget_package(self, package_id: str) -> dict[str, Any] | None:
        self.calls.append(package_id)
        return dict(self._result) if isinstance(self._result, dict) else None


# ---------------------------------------------------------------------------
# badge coordinate extraction
# ---------------------------------------------------------------------------


def test_extract_coords_nuget_link_and_image() -> None:
    badges = parse_badges("[![n](img)](https://www.nuget.org/packages/Polly)")
    assert extract_registry_coords(badges)["nuget"] == "Polly"
    badges2 = parse_badges("[![n](https://img.shields.io/nuget/v/Serilog.svg)](x)")
    assert extract_registry_coords(badges2)["nuget"] == "Serilog"


def test_extract_coords_nuget_dotted_name_not_truncated() -> None:
    # A `.svg` is stripped but a legit `.Json` suffix must be kept.
    badges = parse_badges(
        "[![n](https://img.shields.io/nuget/v/Newtonsoft.Json.svg)]"
        "(https://www.nuget.org/packages/Newtonsoft.Json)",
    )
    assert extract_registry_coords(badges)["nuget"] == "Newtonsoft.Json"


# ---------------------------------------------------------------------------
# PackageRegistryProvider.get_nuget_package
# ---------------------------------------------------------------------------


def test_get_nuget_package_thin_dict_no_repository() -> None:
    pkg = PackageRegistryProvider(session=_nuget_session()).get_nuget_package(
        "Newtonsoft.Json",
    )
    assert pkg is not None
    assert pkg["name"] == "Newtonsoft.Json"
    assert pkg["latest_version"] == "13.0.4"
    assert pkg["versions"] == ["11.0.1", "12.0.3", "13.0.4"]
    assert len(pkg["versions"]) == _EXPECTED_NUGET_VERSIONS
    assert pkg["latest_release_date"] == "2025-09-16T08:13:09.313+00:00"
    assert pkg["repository_url"] is None  # no Source-Link repository → name_only
    assert pkg["registry_url"] == "https://www.nuget.org/packages/Newtonsoft.Json"


def test_get_nuget_package_repository_from_source_link() -> None:
    session = _nuget_session(repository={"type": "git", "url": "https://github.com/acme/tool"})
    pkg = PackageRegistryProvider(session=session).get_nuget_package("Newtonsoft.Json")
    assert pkg is not None
    assert pkg["repository_url"] == "https://github.com/acme/tool"


def test_get_nuget_package_not_found_returns_none() -> None:
    session = _NuGetSession(search={"data": []}, versions={}, registration={})
    assert PackageRegistryProvider(session=session).get_nuget_package("Nope") is None


# ---------------------------------------------------------------------------
# context_gather link policy
# ---------------------------------------------------------------------------


def _enrich(full_name: str, provider: Any, readme: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    providers = ProviderSet(github=MockGitHubProvider(), package_registry=provider)
    _enrich_repository_metadata_with_registry_packages(
        full_name=full_name,
        aux_files={},
        repository_metadata=metadata,
        providers=providers,
        warnings=[],
        readme=readme,
    )
    return metadata


_README = "[![n](img)](https://www.nuget.org/packages/Acme.Tool)"


def test_context_gather_nuget_verified() -> None:
    provider = _FakeNuGetProvider({
        "name": "Acme.Tool", "repository_url": "https://github.com/acme/tool",
    })
    meta = _enrich("acme/tool", provider, _README)
    assert provider.calls == ["Acme.Tool"]
    assert meta["nuget_package"]["link"] == "verified"


def test_context_gather_nuget_name_only_when_no_repo() -> None:
    provider = _FakeNuGetProvider({"name": "Acme.Tool", "repository_url": None})
    meta = _enrich("acme/tool", provider, _README)
    assert meta["nuget_package"]["link"] == "name_only"


def test_context_gather_nuget_mismatch_dropped() -> None:
    provider = _FakeNuGetProvider({
        "name": "Acme.Tool", "repository_url": "https://github.com/other/repo",
    })
    meta = _enrich("acme/tool", provider, _README)
    assert "nuget_package" not in meta


def test_context_gather_nuget_absent_without_badge() -> None:
    provider = _FakeNuGetProvider({"name": "x"})
    meta = _enrich("acme/tool", provider, "# no badges here")
    assert provider.calls == []
    assert "nuget_package" not in meta


# ---------------------------------------------------------------------------
# repository_agent emission
# ---------------------------------------------------------------------------


def _stub_context(*, nuget_package: dict[str, Any] | None) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "name": "tool", "full_name": "acme/tool",
        "owner": {"login": "acme", "type": "Organization"},
        "created_at": "2023-01-01T00:00:00Z", "license": {"spdx_id": "MIT"},
        "fork": False, "source": {"full_name": None},
    }
    if nuget_package is not None:
        metadata["nuget_package"] = nuget_package
    return {
        "full_name": "acme/tool", "metadata": metadata,
        "readme_content": "", "contributors": [{"login": "alice"}],
        "languages": {"C#": 1}, "aux_files": {},
    }


def test_repository_agent_emits_nuget_scalars() -> None:
    agent = RepositoryAgentV2()
    providers = ProviderSet(github=MockGitHubProvider())
    result = asyncio.run(agent.run(
        {"full_name": "acme/tool", "repository_context": _stub_context(nuget_package={
            "name": "Acme.Tool", "latest_version": "13.0.4",
            "versions": ["12.0.3", "13.0.4"],
            "latest_release_date": "2025-09-16T08:13:09.313+00:00",
            "registry_url": "https://www.nuget.org/packages/Acme.Tool",
            "link": "name_only",
        })}, providers))
    raw = result.raw_output
    assert raw["_nuget_package"] == "Acme.Tool"
    assert raw["_nuget_latest_version"] == "13.0.4"
    assert raw["_nuget_versions"] == ["12.0.3", "13.0.4"]
    assert raw["_nuget_link"] == "name_only"


def test_repository_agent_nuget_scalars_none_when_absent() -> None:
    agent = RepositoryAgentV2()
    providers = ProviderSet(github=MockGitHubProvider())
    result = asyncio.run(agent.run(
        {"full_name": "acme/tool", "repository_context": _stub_context(nuget_package=None)},
        providers,
    ))
    raw = result.raw_output
    assert raw["_nuget_package"] is None
    assert raw["_nuget_latest_version"] is None
    assert raw["_nuget_link"] is None
