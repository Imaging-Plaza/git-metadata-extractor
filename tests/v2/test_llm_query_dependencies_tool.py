from __future__ import annotations

from typing import Any

from git_metadata_extractor.agents.llm.agent_tools.query_dependencies import (
    make_query_dependencies_tool,
)


class _FakeGitHubProvider:
    def __init__(self, sbom: list[dict[str, Any]] | None) -> None:
        self.calls: list[str] = []
        self._sbom = sbom

    def get_repository_sbom(self, full_name: str) -> list[dict[str, Any]] | None:
        self.calls.append(full_name)
        return self._sbom


_SAMPLE_SBOM = [
    {"name": "requests", "ecosystem": "pypi", "version": "2.31.0", "spdxId": "a"},
    {"name": "numpy", "ecosystem": "pypi", "version": "1.26.0", "spdxId": "b"},
    {"name": "left-pad", "ecosystem": "npm", "version": "1.3.0", "spdxId": "c"},
    {"name": "react", "ecosystem": "npm", "version": "18.2.0", "spdxId": "d"},
]


def test_query_dependencies_returns_full_list_when_no_filters() -> None:
    provider = _FakeGitHubProvider(_SAMPLE_SBOM)
    tool = make_query_dependencies_tool(provider)

    result = tool.function("octocat/Hello-World")

    assert provider.calls == ["octocat/Hello-World"]
    assert result == _SAMPLE_SBOM
    # Returned entries are copies — caller mutating them must not corrupt the
    # provider's cached payload.
    result[0]["name"] = "tampered"
    assert _SAMPLE_SBOM[0]["name"] == "requests"


def test_query_dependencies_filters_by_ecosystem_case_insensitively() -> None:
    tool = make_query_dependencies_tool(_FakeGitHubProvider(_SAMPLE_SBOM))

    result = tool.function("octocat/Hello-World", ecosystem="PYPI")

    assert [entry["name"] for entry in result] == ["requests", "numpy"]


def test_query_dependencies_filters_by_name_substring_case_insensitively() -> None:
    tool = make_query_dependencies_tool(_FakeGitHubProvider(_SAMPLE_SBOM))

    result = tool.function("octocat/Hello-World", name_contains="REACT")

    assert [entry["name"] for entry in result] == ["react"]


def test_query_dependencies_combines_filters() -> None:
    tool = make_query_dependencies_tool(_FakeGitHubProvider(_SAMPLE_SBOM))

    result = tool.function(
        "octocat/Hello-World",
        ecosystem="npm",
        name_contains="pad",
    )

    assert [entry["name"] for entry in result] == ["left-pad"]


def test_query_dependencies_respects_limit() -> None:
    tool = make_query_dependencies_tool(_FakeGitHubProvider(_SAMPLE_SBOM))

    result = tool.function("octocat/Hello-World", limit=2)

    assert len(result) == 2
    assert [entry["name"] for entry in result] == ["requests", "numpy"]


def test_query_dependencies_returns_empty_list_when_no_sbom() -> None:
    tool = make_query_dependencies_tool(_FakeGitHubProvider(None))

    result = tool.function("private/repo")

    assert result == []


def test_query_dependencies_returns_empty_list_when_sbom_is_empty() -> None:
    tool = make_query_dependencies_tool(_FakeGitHubProvider([]))

    result = tool.function("octocat/Hello-World")

    assert result == []


def test_query_dependencies_clamps_limit_to_max() -> None:
    big_sbom = [
        {"name": f"pkg{i}", "ecosystem": "pypi", "version": "1.0", "spdxId": f"id{i}"}
        for i in range(1000)
    ]
    tool = make_query_dependencies_tool(_FakeGitHubProvider(big_sbom))

    result = tool.function("octocat/Hello-World", limit=10000)

    assert len(result) == 500
