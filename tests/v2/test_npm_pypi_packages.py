"""Tests for npm + PyPI package discovery (manifest-linked).

Covers (no real network — a fake session / fake provider is injected):
  - parse_npm_name / parse_pypi_name: valid, scoped, poetry, setup.cfg
    fallback, missing, malformed, private.
  - repo_url_matches: git+https / ssh / scp / trailing slash / .git /
    case-insensitive / non-github / mismatch.
  - PackageRegistryProvider: canned npm + PyPI JSON → thin dict; 404 → None.
  - summarize_registry_package: full dict + None input (all-None).
  - context_gather link policy: verified vs name_only vs mismatch-dropped.
  - repository_agent: emits flat `_npm_*` / `_pypi_*`; all-None when absent
    or provider None.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from src.v2.agents import ProviderSet, RepositoryAgentV2
from src.v2.agents.rule_based._repo_signals import (
    parse_npm_name,
    parse_pypi_name,
    repo_url_matches,
    summarize_registry_package,
)
from src.v2.ingest.providers.mock_github import MockGitHubProvider
from src.v2.ingest.providers.package_registry_provider import (
    PackageRegistryProvider,
)

# ---------------------------------------------------------------------------
# Fakes — no real network anywhere in this module.
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        if isinstance(self._payload, ValueError):
            raise self._payload
        return self._payload


class FakeSession:
    """A `requests`-like session returning canned responses keyed by URL.

    `routes` maps an exact URL → `_FakeResponse`. Any unmatched URL returns
    a 404 so unexpected calls degrade to None rather than raising.
    """

    def __init__(self, routes: dict[str, _FakeResponse]) -> None:
        self._routes = routes
        self.requested: list[str] = []

    def get(self, url: str, timeout: float | None = None) -> _FakeResponse:  # noqa: ARG002
        self.requested.append(url)
        return self._routes.get(url, _FakeResponse(404, None))


class FakePackageRegistryProvider:
    """A fake `package_registry` provider returning canned thin dicts."""

    def __init__(
        self,
        *,
        npm: dict[str, Any] | None = None,
        pypi: dict[str, Any] | None = None,
    ) -> None:
        self._npm = npm
        self._pypi = pypi
        self.npm_calls: list[str] = []
        self.pypi_calls: list[str] = []

    def get_npm_package(self, name: str) -> dict[str, Any] | None:
        self.npm_calls.append(name)
        return dict(self._npm) if isinstance(self._npm, dict) else None

    def get_pypi_package(self, name: str) -> dict[str, Any] | None:
        self.pypi_calls.append(name)
        return dict(self._pypi) if isinstance(self._pypi, dict) else None


# Canned registry JSON --------------------------------------------------------

_NPM_REGISTRY_JSON = {
    "name": "@acme/tool",
    "dist-tags": {"latest": "2.1.0"},
    "versions": {
        "2.1.0": {"repository": {"url": "git+https://github.com/acme/tool.git"}},
        "2.0.0": {},
        "1.0.0": {},
    },
    "time": {
        "2.1.0": "2024-03-01T12:00:00.000Z",
        "2.0.0": "2024-01-15T08:00:00.000Z",
    },
    "repository": {"type": "git", "url": "git+https://github.com/acme/tool.git"},
}

_PYPI_REGISTRY_JSON = {
    "info": {
        "name": "acme-tool",
        "version": "3.4.0",
        "project_urls": {
            "Homepage": "https://acme.example",
            "Source": "https://github.com/acme/acme-tool",
        },
        "home_page": "https://acme.example",
    },
    "releases": {
        "3.4.0": [{"upload_time_iso_8601": "2024-05-01T10:00:00.000000Z"}],
        "3.3.0": [{"upload_time_iso_8601": "2024-02-01T10:00:00.000000Z"}],
    },
    "urls": [{"upload_time_iso_8601": "2024-05-01T10:00:00.000000Z"}],
}


def _npm_session(payload: Any = _NPM_REGISTRY_JSON, status: int = 200) -> FakeSession:
    return FakeSession({
        "https://registry.npmjs.org/@acme%2Ftool": _FakeResponse(status, payload),
        "https://registry.npmjs.org/lodash": _FakeResponse(status, payload),
    })


def _pypi_session(payload: Any = _PYPI_REGISTRY_JSON, status: int = 200) -> FakeSession:
    return FakeSession({
        "https://pypi.org/pypi/acme-tool/json": _FakeResponse(status, payload),
    })


# ---------------------------------------------------------------------------
# parse_npm_name
# ---------------------------------------------------------------------------


def test_parse_npm_name_valid() -> None:
    aux = {"package.json": json.dumps({"name": "lodash", "version": "1.0.0"})}
    assert parse_npm_name(aux) == "lodash"


def test_parse_npm_name_scoped_kept_intact() -> None:
    aux = {"package.json": json.dumps({"name": "@acme/tool"})}
    assert parse_npm_name(aux) == "@acme/tool"


def test_parse_npm_name_missing_returns_none() -> None:
    assert parse_npm_name({"pyproject.toml": "x"}) is None
    assert parse_npm_name({}) is None
    assert parse_npm_name(None) is None


def test_parse_npm_name_malformed_returns_none() -> None:
    aux = {"package.json": "{not valid json"}
    assert parse_npm_name(aux) is None


def test_parse_npm_name_no_name_field_returns_none() -> None:
    aux = {"package.json": json.dumps({"version": "1.0.0"})}
    assert parse_npm_name(aux) is None


def test_parse_npm_name_private_returns_none() -> None:
    aux = {"package.json": json.dumps({"name": "secret", "private": True})}
    assert parse_npm_name(aux) is None


# ---------------------------------------------------------------------------
# parse_pypi_name
# ---------------------------------------------------------------------------


def test_parse_pypi_name_pep621_project_table() -> None:
    aux = {"pyproject.toml": '[project]\nname = "acme-tool"\nversion = "1.0"\n'}
    assert parse_pypi_name(aux) == "acme-tool"


def test_parse_pypi_name_poetry_table() -> None:
    aux = {"pyproject.toml": '[tool.poetry]\nname = "poetry-pkg"\nversion = "1.0"\n'}
    assert parse_pypi_name(aux) == "poetry-pkg"


def test_parse_pypi_name_project_table_wins_over_poetry() -> None:
    aux = {
        "pyproject.toml": (
            '[project]\nname = "pep621-name"\n'
            '[tool.poetry]\nname = "poetry-name"\n'
        ),
    }
    assert parse_pypi_name(aux) == "pep621-name"


def test_parse_pypi_name_setup_cfg_fallback() -> None:
    aux = {"setup.cfg": "[metadata]\nname = cfg-pkg\nversion = 1.0\n"}
    assert parse_pypi_name(aux) == "cfg-pkg"


def test_parse_pypi_name_missing_returns_none() -> None:
    assert parse_pypi_name({"package.json": "{}"}) is None
    assert parse_pypi_name({}) is None
    assert parse_pypi_name(None) is None


def test_parse_pypi_name_malformed_toml_returns_none() -> None:
    aux = {"pyproject.toml": "[project\nname = broken"}
    assert parse_pypi_name(aux) is None


def test_parse_pypi_name_no_name_returns_none() -> None:
    aux = {"pyproject.toml": '[project]\nversion = "1.0"\n'}
    assert parse_pypi_name(aux) is None


# ---------------------------------------------------------------------------
# repo_url_matches
# ---------------------------------------------------------------------------


def test_repo_url_matches_git_plus_https_dot_git() -> None:
    assert repo_url_matches("git+https://github.com/o/r.git", "o/r") is True


def test_repo_url_matches_ssh_scheme() -> None:
    assert repo_url_matches("ssh://git@github.com/o/r", "o/r") is True


def test_repo_url_matches_scp_like() -> None:
    assert repo_url_matches("git@github.com:o/r.git", "o/r") is True


def test_repo_url_matches_git_protocol() -> None:
    assert repo_url_matches("git://github.com/o/r.git", "o/r") is True


def test_repo_url_matches_trailing_slash() -> None:
    assert repo_url_matches("https://github.com/o/r/", "o/r") is True


def test_repo_url_matches_case_insensitive() -> None:
    assert repo_url_matches("https://GitHub.com/O/R", "o/r") is True


def test_repo_url_matches_www_alias() -> None:
    assert repo_url_matches("https://www.github.com/o/r", "o/r") is True


def test_repo_url_matches_non_github_false() -> None:
    assert repo_url_matches("https://gitlab.com/o/r", "o/r") is False


def test_repo_url_matches_lookalike_subdomain_false() -> None:
    # A package whose repository.url points at a github.com *lookalike*
    # subdomain must NOT be accepted as a verified back-reference, even when
    # the owner/repo path matches exactly.
    assert repo_url_matches("https://evil.github.com/o/r", "o/r") is False
    assert repo_url_matches("https://github.com.evil.test/o/r", "o/r") is False


def test_repo_url_matches_mismatch_false() -> None:
    assert repo_url_matches("https://github.com/other/repo", "o/r") is False


def test_repo_url_matches_none_and_empty_false() -> None:
    assert repo_url_matches(None, "o/r") is False
    assert repo_url_matches("", "o/r") is False
    assert repo_url_matches("https://github.com/o/r", "noslash") is False


# ---------------------------------------------------------------------------
# PackageRegistryProvider — injected fake session (no real network)
# ---------------------------------------------------------------------------


def test_provider_get_npm_package_thin_dict() -> None:
    provider = PackageRegistryProvider(session=_npm_session())
    pkg = provider.get_npm_package("@acme/tool")
    assert pkg is not None
    assert pkg["name"] == "@acme/tool"
    assert pkg["latest_version"] == "2.1.0"
    assert pkg["versions"] == ["1.0.0", "2.0.0", "2.1.0"]
    assert pkg["latest_release_date"] == "2024-03-01T12:00:00.000Z"
    assert pkg["repository_url"] == "git+https://github.com/acme/tool.git"
    assert pkg["registry_url"] == "https://www.npmjs.com/package/@acme/tool"


def test_provider_get_npm_package_scoped_url_encoding() -> None:
    session = _npm_session()
    PackageRegistryProvider(session=session).get_npm_package("@acme/tool")
    assert "https://registry.npmjs.org/@acme%2Ftool" in session.requested


def test_provider_get_npm_package_repo_url_from_version_fallback() -> None:
    payload = {
        "name": "noroot",
        "dist-tags": {"latest": "1.0.0"},
        "versions": {
            "1.0.0": {"repository": {"url": "https://github.com/acme/noroot"}},
        },
        "time": {"1.0.0": "2024-01-01T00:00:00Z"},
    }
    session = FakeSession({
        "https://registry.npmjs.org/noroot": _FakeResponse(200, payload),
    })
    pkg = PackageRegistryProvider(session=session).get_npm_package("noroot")
    assert pkg is not None
    assert pkg["repository_url"] == "https://github.com/acme/noroot"


def test_provider_get_npm_package_404_returns_none() -> None:
    provider = PackageRegistryProvider(session=_npm_session(status=404))
    assert provider.get_npm_package("@acme/tool") is None


def test_provider_get_pypi_package_thin_dict() -> None:
    provider = PackageRegistryProvider(session=_pypi_session())
    pkg = provider.get_pypi_package("acme-tool")
    assert pkg is not None
    assert pkg["name"] == "acme-tool"
    assert pkg["latest_version"] == "3.4.0"
    assert pkg["versions"] == ["3.3.0", "3.4.0"]
    assert pkg["latest_release_date"] == "2024-05-01T10:00:00.000000Z"
    assert pkg["repository_url"] == "https://github.com/acme/acme-tool"
    assert pkg["registry_url"] == "https://pypi.org/project/acme-tool/"


def test_provider_get_pypi_package_404_returns_none() -> None:
    provider = PackageRegistryProvider(session=_pypi_session(status=404))
    assert provider.get_pypi_package("acme-tool") is None


def test_provider_get_pypi_home_page_fallback() -> None:
    payload = {
        "info": {
            "name": "homeonly",
            "version": "1.0.0",
            "project_urls": None,
            "home_page": "https://github.com/acme/homeonly",
        },
        "releases": {"1.0.0": [{"upload_time_iso_8601": "2024-01-01T00:00:00Z"}]},
    }
    session = FakeSession({
        "https://pypi.org/pypi/homeonly/json": _FakeResponse(200, payload),
    })
    pkg = PackageRegistryProvider(session=session).get_pypi_package("homeonly")
    assert pkg is not None
    assert pkg["repository_url"] == "https://github.com/acme/homeonly"


def test_provider_transport_error_returns_none() -> None:
    class _BoomSession:
        def get(self, url: str, timeout: float | None = None) -> Any:  # noqa: ARG002
            message = "boom"
            raise ConnectionError(message)

    provider = PackageRegistryProvider(session=_BoomSession())
    assert provider.get_npm_package("x") is None
    assert provider.get_pypi_package("y") is None


# ---------------------------------------------------------------------------
# summarize_registry_package
# ---------------------------------------------------------------------------


def test_summarize_registry_package_full_dict() -> None:
    pkg = {
        "name": "@acme/tool",
        "latest_version": "2.1.0",
        "versions": ["1.0.0", "2.0.0", "2.1.0"],
        "latest_release_date": "2024-03-01T12:00:00Z",
        "repository_url": "git+https://github.com/acme/tool.git",
        "registry_url": "https://www.npmjs.com/package/@acme/tool",
        "link": "verified",
    }
    out = summarize_registry_package(pkg)
    assert out == {
        "package": "@acme/tool",
        "latest_version": "2.1.0",
        "versions": ["1.0.0", "2.0.0", "2.1.0"],
        "latest_release_date": "2024-03-01T12:00:00Z",
        "registry_url": "https://www.npmjs.com/package/@acme/tool",
        "link": "verified",
    }


def test_summarize_registry_package_none_all_none() -> None:
    out = summarize_registry_package(None)
    assert out == {
        "package": None,
        "latest_version": None,
        "versions": None,
        "latest_release_date": None,
        "registry_url": None,
        "link": None,
    }


def test_summarize_registry_package_not_dict_all_none() -> None:
    assert summarize_registry_package("nope")["package"] is None


# ---------------------------------------------------------------------------
# context_gather — link policy (verified / name_only / mismatch-dropped)
# ---------------------------------------------------------------------------

from src.v2.pipeline.stages.context_gather import (
    _enrich_repository_metadata_with_registry_packages,
)


def _run_enrich(
    *,
    full_name: str,
    aux_files: dict[str, str],
    provider: Any,
) -> tuple[dict[str, Any], list[str]]:
    metadata: dict[str, Any] = {}
    warnings: list[str] = []
    providers = ProviderSet(github=MockGitHubProvider(), package_registry=provider)
    _enrich_repository_metadata_with_registry_packages(
        full_name=full_name,
        aux_files=aux_files,
        repository_metadata=metadata,
        providers=providers,
        warnings=warnings,
    )
    return metadata, warnings


def test_context_gather_npm_verified() -> None:
    provider = FakePackageRegistryProvider(
        npm={
            "name": "@acme/tool",
            "repository_url": "git+https://github.com/acme/tool.git",
        },
    )
    metadata, _ = _run_enrich(
        full_name="acme/tool",
        aux_files={"package.json": json.dumps({"name": "@acme/tool"})},
        provider=provider,
    )
    assert metadata["npm_package"]["link"] == "verified"


def test_context_gather_npm_name_only_when_repo_url_absent() -> None:
    provider = FakePackageRegistryProvider(npm={"name": "lonely", "repository_url": None})
    metadata, _ = _run_enrich(
        full_name="acme/lonely",
        aux_files={"package.json": json.dumps({"name": "lonely"})},
        provider=provider,
    )
    assert metadata["npm_package"]["link"] == "name_only"


def test_context_gather_npm_mismatch_dropped() -> None:
    provider = FakePackageRegistryProvider(
        npm={
            "name": "collision",
            "repository_url": "https://github.com/someone-else/other",
        },
    )
    metadata, _ = _run_enrich(
        full_name="acme/tool",
        aux_files={"package.json": json.dumps({"name": "collision"})},
        provider=provider,
    )
    assert "npm_package" not in metadata


def test_context_gather_pypi_verified() -> None:
    provider = FakePackageRegistryProvider(
        pypi={
            "name": "acme-tool",
            "repository_url": "https://github.com/acme/acme-tool",
        },
    )
    metadata, _ = _run_enrich(
        full_name="acme/acme-tool",
        aux_files={"pyproject.toml": '[project]\nname = "acme-tool"\n'},
        provider=provider,
    )
    assert metadata["pypi_package"]["link"] == "verified"


def test_context_gather_no_provider_noop() -> None:
    metadata: dict[str, Any] = {}
    warnings: list[str] = []
    providers = ProviderSet(github=MockGitHubProvider())  # package_registry None
    _enrich_repository_metadata_with_registry_packages(
        full_name="acme/tool",
        aux_files={"package.json": json.dumps({"name": "@acme/tool"})},
        repository_metadata=metadata,
        providers=providers,
        warnings=warnings,
    )
    assert metadata == {}
    assert warnings == []


# ---------------------------------------------------------------------------
# repository_agent — flat `_npm_*` / `_pypi_*` emission
# ---------------------------------------------------------------------------


def _make_repo_context(
    *,
    npm_package: dict[str, Any] | None = None,
    pypi_package: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "name": "tool",
        "full_name": "acme/tool",
        "owner": {"login": "acme", "type": "Organization"},
        "created_at": "2023-01-01T00:00:00Z",
        "license": {"spdx_id": "MIT"},
        "fork": False,
        "source": {"full_name": None},
    }
    if npm_package is not None:
        metadata["npm_package"] = npm_package
    if pypi_package is not None:
        metadata["pypi_package"] = pypi_package
    return {
        "full_name": "acme/tool",
        "metadata": metadata,
        "readme_content": "",
        "contributors": [{"login": "alice"}],
        "languages": {"Python": 1},
        "aux_files": {},
    }


def test_repository_agent_emits_npm_and_pypi_flat_scalars() -> None:
    agent = RepositoryAgentV2()
    providers = ProviderSet(github=MockGitHubProvider())
    npm = {
        "name": "@acme/tool",
        "latest_version": "2.1.0",
        "versions": ["1.0.0", "2.1.0"],
        "latest_release_date": "2024-03-01T12:00:00Z",
        "registry_url": "https://www.npmjs.com/package/@acme/tool",
        "link": "verified",
    }
    pypi = {
        "name": "acme-tool",
        "latest_version": "3.4.0",
        "versions": ["3.3.0", "3.4.0"],
        "latest_release_date": "2024-05-01T10:00:00Z",
        "registry_url": "https://pypi.org/project/acme-tool/",
        "link": "name_only",
    }
    result = asyncio.run(
        agent.run(
            {
                "full_name": "acme/tool",
                "repository_context": _make_repo_context(
                    npm_package=npm, pypi_package=pypi,
                ),
            },
            providers,
        ),
    )
    raw = result.raw_output
    assert raw["_npm_package"] == "@acme/tool"
    assert raw["_npm_latest_version"] == "2.1.0"
    assert raw["_npm_versions"] == ["1.0.0", "2.1.0"]
    assert raw["_npm_latest_release_date"] == "2024-03-01T12:00:00Z"
    assert raw["_npm_registry_url"] == "https://www.npmjs.com/package/@acme/tool"
    assert raw["_npm_link"] == "verified"
    assert raw["_pypi_package"] == "acme-tool"
    assert raw["_pypi_latest_version"] == "3.4.0"
    assert raw["_pypi_link"] == "name_only"


def test_repository_agent_npm_pypi_all_none_when_absent() -> None:
    agent = RepositoryAgentV2()
    providers = ProviderSet(github=MockGitHubProvider())
    result = asyncio.run(
        agent.run(
            {
                "full_name": "acme/tool",
                "repository_context": _make_repo_context(),
            },
            providers,
        ),
    )
    raw = result.raw_output
    for key in (
        "_npm_package",
        "_npm_latest_version",
        "_npm_versions",
        "_npm_latest_release_date",
        "_npm_registry_url",
        "_npm_link",
        "_pypi_package",
        "_pypi_latest_version",
        "_pypi_versions",
        "_pypi_latest_release_date",
        "_pypi_registry_url",
        "_pypi_link",
    ):
        assert raw[key] is None, key
