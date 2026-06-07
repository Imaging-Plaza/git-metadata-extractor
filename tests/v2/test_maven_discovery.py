"""Tests for Maven Central artifact discovery (pom.xml / badge linked).

No real network — a fake session / fake provider is injected.
"""
from __future__ import annotations

import asyncio
from typing import Any

from src.v2.agents import ProviderSet, RepositoryAgentV2
from src.v2.agents.rule_based._repo_signals import (
    extract_registry_coords,
    parse_badges,
    parse_maven_coords,
)
from src.v2.ingest.providers.mock_github import MockGitHubProvider
from src.v2.ingest.providers.package_registry_provider import (
    PackageRegistryProvider,
    _ms_to_iso,
)
from src.v2.pipeline.stages.context_gather import (
    _enrich_repository_metadata_with_registry_packages,
)

_EXPECTED_MAVEN_VERSIONS = 3

_POM = (
    '<project xmlns="http://maven.apache.org/POM/4.0.0">'
    "<modelVersion>4.0.0</modelVersion>"
    "<groupId>com.acme</groupId><artifactId>widget</artifactId>"
    "</project>"
)
_POM_PARENT_GROUP = (
    '<project xmlns="http://maven.apache.org/POM/4.0.0">'
    "<parent><groupId>com.acme</groupId><artifactId>parent</artifactId></parent>"
    "<artifactId>widget</artifactId></project>"
)


class _FakeResp:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        if isinstance(self._payload, ValueError):
            raise self._payload
        return self._payload


class _MavenSession:
    """Returns the `latest` payload for the rows=1 query and the `gav`
    payload for the core=gav query, keyed on the `core=gav` substring."""

    def __init__(self, *, latest: Any, gav: Any) -> None:
        self._latest = latest
        self._gav = gav
        self.requested: list[str] = []

    def get(self, url: str, timeout: float | None = None, headers: Any = None) -> _FakeResp:  # noqa: ARG002
        self.requested.append(url)
        payload = self._gav if "core=gav" in url else self._latest
        return _FakeResp(200, payload)


def _solr(docs: list[dict[str, Any]]) -> dict[str, Any]:
    return {"response": {"numFound": len(docs), "docs": docs}}


def _maven_session() -> _MavenSession:
    return _MavenSession(
        latest=_solr([{
            "g": "com.acme", "a": "widget",
            "latestVersion": "2.1.0", "timestamp": 1744651522422, "versionCount": 3,
        }]),
        gav=_solr([
            {"v": "2.1.0", "timestamp": 1744651522422},
            {"v": "2.0.0", "timestamp": 1742918069784},
            {"v": "1.0.0", "timestamp": 1700000000000},
        ]),
    )


class _FakeMavenProvider:
    def __init__(self, result: dict[str, Any] | None) -> None:
        self._result = result
        self.calls: list[tuple[str, str]] = []

    def get_maven_package(self, group_id: str, artifact_id: str) -> dict[str, Any] | None:
        self.calls.append((group_id, artifact_id))
        return dict(self._result) if isinstance(self._result, dict) else None


# ---------------------------------------------------------------------------
# parse_maven_coords
# ---------------------------------------------------------------------------


def test_parse_maven_coords_basic() -> None:
    assert parse_maven_coords({"pom.xml": _POM}) == ("com.acme", "widget")


def test_parse_maven_coords_parent_group_fallback() -> None:
    assert parse_maven_coords({"pom.xml": _POM_PARENT_GROUP}) == ("com.acme", "widget")


def test_parse_maven_coords_no_artifact_returns_none() -> None:
    pom = '<project xmlns="http://maven.apache.org/POM/4.0.0"><groupId>g</groupId></project>'
    assert parse_maven_coords({"pom.xml": pom}) is None


def test_parse_maven_coords_malformed_returns_none() -> None:
    assert parse_maven_coords({"pom.xml": "<project><artifactId>x"}) is None


def test_parse_maven_coords_missing_returns_none() -> None:
    assert parse_maven_coords({"package.json": "{}"}) is None
    assert parse_maven_coords(None) is None


# ---------------------------------------------------------------------------
# badge coordinate extraction
# ---------------------------------------------------------------------------


def test_extract_coords_maven_shields_and_link() -> None:
    badges = parse_badges(
        "[![mvn](https://img.shields.io/maven-central/v/org.foo/bar.svg)](x)",
    )
    assert extract_registry_coords(badges)["maven"] == ("org.foo", "bar")
    badges2 = parse_badges(
        "[![mvn](img)](https://central.sonatype.com/artifact/org.baz/qux)",
    )
    assert extract_registry_coords(badges2)["maven"] == ("org.baz", "qux")


# ---------------------------------------------------------------------------
# _ms_to_iso
# ---------------------------------------------------------------------------


def test_ms_to_iso() -> None:
    assert _ms_to_iso(1744651522422).startswith("2025-04-14T")
    assert _ms_to_iso(None) is None
    assert _ms_to_iso("nope") is None
    assert _ms_to_iso(True) is None  # noqa: FBT003 — bool must be rejected, not treated as epoch


# ---------------------------------------------------------------------------
# PackageRegistryProvider.get_maven_package
# ---------------------------------------------------------------------------


def test_get_maven_package_thin_dict() -> None:
    pkg = PackageRegistryProvider(session=_maven_session()).get_maven_package(
        "com.acme", "widget",
    )
    assert pkg is not None
    assert pkg["name"] == "com.acme:widget"
    assert pkg["group_id"] == "com.acme"
    assert pkg["artifact_id"] == "widget"
    assert pkg["latest_version"] == "2.1.0"
    assert pkg["versions"] == ["2.1.0", "2.0.0", "1.0.0"]
    assert len(pkg["versions"]) == _EXPECTED_MAVEN_VERSIONS
    assert pkg["latest_release_date"].startswith("2025-04-14T")
    assert pkg["repository_url"] is None
    assert pkg["registry_url"] == "https://central.sonatype.com/artifact/com.acme/widget"


def test_get_maven_package_not_found_returns_none() -> None:
    session = _MavenSession(latest=_solr([]), gav=_solr([]))
    assert PackageRegistryProvider(session=session).get_maven_package("x", "y") is None


# ---------------------------------------------------------------------------
# context_gather link policy (manifest → verified, badge → name_only)
# ---------------------------------------------------------------------------


def _enrich(full_name: str, aux_files: dict[str, str], provider: Any, readme: str | None) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    providers = ProviderSet(github=MockGitHubProvider(), package_registry=provider)
    _enrich_repository_metadata_with_registry_packages(
        full_name=full_name,
        aux_files=aux_files,
        repository_metadata=metadata,
        providers=providers,
        warnings=[],
        readme=readme,
    )
    return metadata


def test_context_gather_maven_from_pom_verified() -> None:
    provider = _FakeMavenProvider({"name": "com.acme:widget", "group_id": "com.acme"})
    meta = _enrich("acme/widget", {"pom.xml": _POM}, provider, None)
    assert provider.calls == [("com.acme", "widget")]
    assert meta["maven_package"]["link"] == "verified"


def test_context_gather_maven_from_badge_name_only() -> None:
    provider = _FakeMavenProvider({"name": "org.foo:bar"})
    readme = "[![mvn](https://img.shields.io/maven-central/v/org.foo/bar.svg)](x)"
    meta = _enrich("acme/widget", {}, provider, readme)
    assert provider.calls == [("org.foo", "bar")]
    assert meta["maven_package"]["link"] == "name_only"


def test_context_gather_maven_absent() -> None:
    provider = _FakeMavenProvider({"name": "x"})
    meta = _enrich("acme/widget", {"package.json": "{}"}, provider, None)
    assert provider.calls == []
    assert "maven_package" not in meta


# ---------------------------------------------------------------------------
# repository_agent emission
# ---------------------------------------------------------------------------


def _stub_context(*, maven_package: dict[str, Any] | None) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "name": "widget", "full_name": "acme/widget",
        "owner": {"login": "acme", "type": "Organization"},
        "created_at": "2023-01-01T00:00:00Z", "license": {"spdx_id": "MIT"},
        "fork": False, "source": {"full_name": None},
    }
    if maven_package is not None:
        metadata["maven_package"] = maven_package
    return {
        "full_name": "acme/widget", "metadata": metadata,
        "readme_content": "", "contributors": [{"login": "alice"}],
        "languages": {"Java": 1}, "aux_files": {},
    }


def test_repository_agent_emits_maven_scalars() -> None:
    agent = RepositoryAgentV2()
    providers = ProviderSet(github=MockGitHubProvider())
    result = asyncio.run(agent.run(
        {"full_name": "acme/widget", "repository_context": _stub_context(maven_package={
            "name": "com.acme:widget", "group_id": "com.acme", "artifact_id": "widget",
            "latest_version": "2.1.0", "versions": ["2.1.0", "2.0.0"],
            "latest_release_date": "2025-04-14T17:25:22Z",
            "registry_url": "https://central.sonatype.com/artifact/com.acme/widget",
            "link": "verified",
        })}, providers))
    raw = result.raw_output
    assert raw["_maven_package"] == "com.acme:widget"
    assert raw["_maven_group_id"] == "com.acme"
    assert raw["_maven_artifact_id"] == "widget"
    assert raw["_maven_latest_version"] == "2.1.0"
    assert raw["_maven_link"] == "verified"


def test_repository_agent_maven_scalars_none_when_absent() -> None:
    agent = RepositoryAgentV2()
    providers = ProviderSet(github=MockGitHubProvider())
    result = asyncio.run(agent.run(
        {"full_name": "acme/widget", "repository_context": _stub_context(maven_package=None)},
        providers,
    ))
    raw = result.raw_output
    assert raw["_maven_package"] is None
    assert raw["_maven_group_id"] is None
    assert raw["_maven_link"] is None
