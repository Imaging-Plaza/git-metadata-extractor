"""Tests for Go module discovery (manifest-linked, via the module proxy).

No real network — a fake session / fake provider is injected.
"""
from __future__ import annotations

import asyncio
from typing import Any

from src.v2.agents import ProviderSet, RepositoryAgentV2
from src.v2.agents.rule_based._repo_signals import parse_go_module
from src.v2.ingest.providers.mock_github import MockGitHubProvider
from src.v2.ingest.providers.package_registry_provider import (
    PackageRegistryProvider,
    _go_escape,
    _go_repository_url,
)
from src.v2.pipeline.stages.context_gather import (
    _enrich_repository_metadata_with_registry_packages,
)

_EXPECTED_GO_VERSIONS = 3


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, status_code: int, *, json_payload: Any = None, text: str | None = None) -> None:
        self.status_code = status_code
        self._json = json_payload
        self.text = text if text is not None else ""

    def json(self) -> Any:
        if isinstance(self._json, ValueError):
            raise self._json
        return self._json


class _FakeSession:
    def __init__(self, routes: dict[str, _FakeResp]) -> None:
        self._routes = routes
        self.requested: list[str] = []

    def get(
        self,
        url: str,
        timeout: float | None = None,  # noqa: ARG002
        headers: dict[str, str] | None = None,  # noqa: ARG002
    ) -> _FakeResp:
        self.requested.append(url)
        return self._routes.get(url, _FakeResp(404))


def _go_session(module: str = "github.com/acme/tool") -> _FakeSession:
    esc = _go_escape(module)
    return _FakeSession({
        f"https://proxy.golang.org/{esc}/@latest": _FakeResp(
            200, json_payload={"Version": "v1.4.0", "Time": "2024-05-01T10:00:00Z"},
        ),
        f"https://proxy.golang.org/{esc}/@v/list": _FakeResp(
            200, text="v1.2.0\nv1.3.0\nv1.4.0\n",
        ),
    })


class _FakeGoProvider:
    def __init__(self, result: dict[str, Any] | None) -> None:
        self._result = result
        self.calls: list[str] = []

    def get_go_module(self, module: str) -> dict[str, Any] | None:
        self.calls.append(module)
        return dict(self._result) if isinstance(self._result, dict) else None


# ---------------------------------------------------------------------------
# parse_go_module
# ---------------------------------------------------------------------------


def test_parse_go_module_basic() -> None:
    assert parse_go_module({"go.mod": "module github.com/acme/tool\n\ngo 1.22\n"}) == "github.com/acme/tool"


def test_parse_go_module_with_inline_comment_and_major_version() -> None:
    aux = {"go.mod": "module github.com/acme/tool/v2 // root module\n\ngo 1.22\n"}
    assert parse_go_module(aux) == "github.com/acme/tool/v2"


def test_parse_go_module_missing_returns_none() -> None:
    assert parse_go_module({"package.json": "{}"}) is None
    assert parse_go_module({}) is None
    assert parse_go_module(None) is None


def test_parse_go_module_no_module_directive_returns_none() -> None:
    assert parse_go_module({"go.mod": "go 1.22\nrequire ()\n"}) is None


# ---------------------------------------------------------------------------
# _go_escape / _go_repository_url
# ---------------------------------------------------------------------------


def test_go_escape_lowercases_capitals() -> None:
    assert _go_escape("github.com/BurntSushi/toml") == "github.com/!burnt!sushi/toml"
    assert _go_escape("github.com/acme/tool") == "github.com/acme/tool"


def test_go_repository_url_github_with_major_suffix() -> None:
    assert _go_repository_url("github.com/acme/tool/v2") == "https://github.com/acme/tool"
    assert _go_repository_url("github.com/acme/tool") == "https://github.com/acme/tool"


def test_go_repository_url_vanity_returns_none() -> None:
    assert _go_repository_url("gopkg.in/yaml.v3") is None
    assert _go_repository_url("example.com/x/y") is None
    assert _go_repository_url("github.com/acme") is None  # too few segments


# ---------------------------------------------------------------------------
# PackageRegistryProvider.get_go_module
# ---------------------------------------------------------------------------


def test_get_go_module_thin_dict() -> None:
    provider = PackageRegistryProvider(session=_go_session())
    pkg = provider.get_go_module("github.com/acme/tool")
    assert pkg is not None
    assert pkg["name"] == "github.com/acme/tool"
    assert pkg["latest_version"] == "v1.4.0"
    assert pkg["versions"] == ["v1.2.0", "v1.3.0", "v1.4.0"]
    assert len(pkg["versions"]) == _EXPECTED_GO_VERSIONS
    assert pkg["latest_release_date"] == "2024-05-01T10:00:00Z"
    assert pkg["repository_url"] == "https://github.com/acme/tool"
    assert pkg["registry_url"] == "https://pkg.go.dev/github.com/acme/tool"


def test_get_go_module_unknown_returns_none() -> None:
    # Empty session → both @latest and @v/list 404 → None.
    provider = PackageRegistryProvider(session=_FakeSession({}))
    assert provider.get_go_module("github.com/acme/tool") is None


def test_get_go_module_vanity_repository_url_none() -> None:
    module = "gopkg.in/yaml.v3"
    esc = _go_escape(module)
    session = _FakeSession({
        f"https://proxy.golang.org/{esc}/@latest": _FakeResp(
            200, json_payload={"Version": "v3.0.1", "Time": "2022-05-27T00:00:00Z"},
        ),
        f"https://proxy.golang.org/{esc}/@v/list": _FakeResp(200, text="v3.0.0\nv3.0.1\n"),
    })
    pkg = PackageRegistryProvider(session=session).get_go_module(module)
    assert pkg is not None
    assert pkg["repository_url"] is None  # vanity → name_only downstream


# ---------------------------------------------------------------------------
# context_gather link policy
# ---------------------------------------------------------------------------


def _enrich(full_name: str, aux_files: dict[str, str], provider: Any) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    providers = ProviderSet(github=MockGitHubProvider(), package_registry=provider)
    _enrich_repository_metadata_with_registry_packages(
        full_name=full_name,
        aux_files=aux_files,
        repository_metadata=metadata,
        providers=providers,
        warnings=[],
        readme=None,
    )
    return metadata


def test_context_gather_go_module_verified() -> None:
    provider = _FakeGoProvider({
        "name": "github.com/acme/tool",
        "latest_version": "v1.4.0",
        "repository_url": "https://github.com/acme/tool",
    })
    meta = _enrich("acme/tool", {"go.mod": "module github.com/acme/tool\n"}, provider)
    assert provider.calls == ["github.com/acme/tool"]
    assert meta["go_module"]["link"] == "verified"


def test_context_gather_go_module_name_only_when_no_repo_url() -> None:
    provider = _FakeGoProvider({"name": "example.com/acme/tool", "repository_url": None})
    meta = _enrich("acme/tool", {"go.mod": "module example.com/acme/tool\n"}, provider)
    assert meta["go_module"]["link"] == "name_only"


def test_context_gather_go_module_mismatch_dropped() -> None:
    provider = _FakeGoProvider({
        "name": "github.com/other/repo",
        "repository_url": "https://github.com/other/repo",
    })
    meta = _enrich("acme/tool", {"go.mod": "module github.com/other/repo\n"}, provider)
    assert "go_module" not in meta  # back-ref points elsewhere → dropped


def test_context_gather_no_gomod_no_lookup() -> None:
    provider = _FakeGoProvider({"name": "x"})
    meta = _enrich("acme/tool", {"package.json": "{}"}, provider)
    assert provider.calls == []
    assert "go_module" not in meta


# ---------------------------------------------------------------------------
# repository_agent emission
# ---------------------------------------------------------------------------


def _make_stub_context(*, go_module: dict[str, Any] | None) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "name": "tool",
        "full_name": "acme/tool",
        "owner": {"login": "acme", "type": "Organization"},
        "created_at": "2023-01-01T00:00:00Z",
        "license": {"spdx_id": "MIT"},
        "fork": False,
        "source": {"full_name": None},
    }
    if go_module is not None:
        metadata["go_module"] = go_module
    return {
        "full_name": "acme/tool",
        "metadata": metadata,
        "readme_content": "",
        "contributors": [{"login": "alice"}],
        "languages": {"Go": 1},
        "aux_files": {},
    }


def test_repository_agent_emits_go_scalars() -> None:
    agent = RepositoryAgentV2()
    providers = ProviderSet(github=MockGitHubProvider())
    result = asyncio.run(
        agent.run(
            {
                "full_name": "acme/tool",
                "repository_context": _make_stub_context(go_module={
                    "name": "github.com/acme/tool",
                    "latest_version": "v1.4.0",
                    "versions": ["v1.2.0", "v1.3.0", "v1.4.0"],
                    "latest_release_date": "2024-05-01T10:00:00Z",
                    "registry_url": "https://pkg.go.dev/github.com/acme/tool",
                    "link": "verified",
                }),
            },
            providers,
        ),
    )
    raw = result.raw_output
    assert raw["_go_package"] == "github.com/acme/tool"
    assert raw["_go_latest_version"] == "v1.4.0"
    assert raw["_go_versions"] == ["v1.2.0", "v1.3.0", "v1.4.0"]
    assert raw["_go_registry_url"] == "https://pkg.go.dev/github.com/acme/tool"
    assert raw["_go_link"] == "verified"


def test_repository_agent_go_scalars_none_when_absent() -> None:
    agent = RepositoryAgentV2()
    providers = ProviderSet(github=MockGitHubProvider())
    result = asyncio.run(
        agent.run(
            {
                "full_name": "acme/tool",
                "repository_context": _make_stub_context(go_module=None),
            },
            providers,
        ),
    )
    raw = result.raw_output
    assert raw["_go_package"] is None
    assert raw["_go_latest_version"] is None
    assert raw["_go_link"] is None
