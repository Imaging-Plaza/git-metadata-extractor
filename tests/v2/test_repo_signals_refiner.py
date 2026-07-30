"""Phase 2 — LLM README enrichment: repo_signals refiner tests.

Covers:
  - extract_doc_candidate_urls (unit tests, no LLM).
  - Refiner wiring via the run_repo_signals_for_entity helper that
    _run_repo_signals_pass calls: apply / shadow / off modes, the
    deterministic-coverage-wins guard, and the no-README skip.

No real LLM calls — the runner is monkeypatched to return a fixed patch.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from git_metadata_extractor.agents.rule_based._repo_signals import extract_doc_candidate_urls
from git_metadata_extractor.pipeline.stages.refine_with_llm import _run_repo_signals_pass


# ---------------------------------------------------------------------------
# 1. extract_doc_candidate_urls
# ---------------------------------------------------------------------------


def test_extract_doc_candidate_urls_basic() -> None:
    readme = (
        "See the docs at https://my-project.readthedocs.io/en/latest/\n"
        "and the pages site at https://orgname.github.io/project/\n"
        "or visit https://github.com/orgname/project (not a doc site)\n"
    )
    urls = extract_doc_candidate_urls(readme)
    assert "https://my-project.readthedocs.io/en/latest/" in urls
    assert "https://orgname.github.io/project/" in urls
    # the bare github.com repo URL should NOT appear
    assert not any("github.com/orgname/project" in u and "github.io" not in u for u in urls)


def test_extract_doc_candidate_urls_deduped() -> None:
    readme = (
        "https://proj.readthedocs.io https://proj.readthedocs.io\n"
        "https://proj.readthedocs.io/en/stable/\n"
    )
    urls = extract_doc_candidate_urls(readme)
    seen = [u for u in urls if "readthedocs.io" in u]
    assert len(seen) == len(set(seen)), "duplicates must be removed"


def test_extract_doc_candidate_urls_none_readme() -> None:
    assert extract_doc_candidate_urls(None) == []


def test_extract_doc_candidate_urls_empty_readme() -> None:
    assert extract_doc_candidate_urls("") == []


def test_extract_doc_candidate_urls_gitbook() -> None:
    readme = "Docs: https://myproject.gitbook.io/docs/"
    urls = extract_doc_candidate_urls(readme)
    assert "https://myproject.gitbook.io/docs/" in urls


def test_extract_doc_candidate_urls_docs_subdomain() -> None:
    readme = "Visit https://docs.myproject.io/guide for details."
    urls = extract_doc_candidate_urls(readme)
    assert "https://docs.myproject.io/guide" in urls


def test_extract_doc_candidate_urls_gitlab_pages() -> None:
    readme = "See https://myorg.gitlab.io/myproject/"
    urls = extract_doc_candidate_urls(readme)
    assert "https://myorg.gitlab.io/myproject/" in urls


def test_extract_doc_candidate_urls_docs_path() -> None:
    readme = "API reference: https://myproject.example.com/docs/api"
    urls = extract_doc_candidate_urls(readme)
    assert "https://myproject.example.com/docs/api" in urls


def test_extract_doc_candidate_urls_no_doc_links() -> None:
    readme = "[![Build](https://travis-ci.org/foo/bar.svg)](https://travis-ci.org/foo/bar)"
    urls = extract_doc_candidate_urls(readme)
    assert urls == []


# ---------------------------------------------------------------------------
# 2. Refiner wiring — monkeypatched runner
# ---------------------------------------------------------------------------


class _FixedPatch:
    """Minimal stand-in for RepoSignalsPatch."""

    def __init__(
        self,
        documentation_urls: list[str] | None = None,
        test_coverage: str | None = None,
    ) -> None:
        self.documentation_urls = documentation_urls or []
        self.test_coverage = test_coverage


_DOC_URLS = ["https://my-proj.readthedocs.io/en/latest/"]
_FIXED_PATCH = _FixedPatch(documentation_urls=_DOC_URLS, test_coverage="80%")


def _make_repo(
    readme: str | None = "See https://my-proj.readthedocs.io/en/latest/",
    test_coverage: str | None = None,
    handle: str = "owner/repo",
) -> dict[str, Any]:
    entity: dict[str, Any] = {
        "type": "schema:SoftwareSourceCode",
        "id": f"https://github.com/{handle}",
        "schema:name": "repo",
        "_test_coverage": test_coverage,
        "_documentation_urls": [],
        "_readme_content": readme,
    }
    return entity


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# --- apply mode ---


def test_apply_sets_documentation_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V2_REPO_SIGNALS_AGENT_MODE", "apply")
    ran: list[int] = []

    async def _stub_runner(inp: Any, **_kw: Any) -> _FixedPatch:
        ran.append(1)
        return _FIXED_PATCH

    monkeypatch.setattr(
        "git_metadata_extractor.pipeline.stages.refine_with_llm.run_repo_signals",
        _stub_runner,
    )

    repo = _make_repo()
    _run(_run_repo_signals_pass(repositories=[repo], gathered_context=_gathered(repo)))
    assert ran, "runner must have been called"
    assert repo.get("_documentation_urls") == _DOC_URLS


def test_apply_sets_test_coverage_when_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V2_REPO_SIGNALS_AGENT_MODE", "apply")

    async def _stub_runner(inp: Any, **_kw: Any) -> _FixedPatch:
        return _FIXED_PATCH

    monkeypatch.setattr(
        "git_metadata_extractor.pipeline.stages.refine_with_llm.run_repo_signals",
        _stub_runner,
    )

    repo = _make_repo(test_coverage=None)
    _run(_run_repo_signals_pass(repositories=[repo], gathered_context=_gathered(repo)))
    assert repo.get("_test_coverage") == "80%"


def test_apply_does_not_override_deterministic_coverage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deterministic hit from Phase 1 must NOT be overridden by the LLM."""
    monkeypatch.setenv("V2_REPO_SIGNALS_AGENT_MODE", "apply")

    async def _stub_runner(inp: Any, **_kw: Any) -> _FixedPatch:
        return _FIXED_PATCH  # patch.test_coverage = "80%"

    monkeypatch.setattr(
        "git_metadata_extractor.pipeline.stages.refine_with_llm.run_repo_signals",
        _stub_runner,
    )

    repo = _make_repo(test_coverage="90%")  # Phase-1 deterministic result
    _run(_run_repo_signals_pass(repositories=[repo], gathered_context=_gathered(repo)))
    assert repo.get("_test_coverage") == "90%", "deterministic hit must not be overridden"


# --- shadow mode ---


def test_shadow_runs_agent_but_does_not_mutate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V2_REPO_SIGNALS_AGENT_MODE", "shadow")
    ran: list[int] = []

    async def _stub_runner(inp: Any, **_kw: Any) -> _FixedPatch:
        ran.append(1)
        return _FIXED_PATCH

    monkeypatch.setattr(
        "git_metadata_extractor.pipeline.stages.refine_with_llm.run_repo_signals",
        _stub_runner,
    )

    repo = _make_repo()
    _run(_run_repo_signals_pass(repositories=[repo], gathered_context=_gathered(repo)))
    assert ran, "runner must have been called in shadow mode"
    assert repo.get("_documentation_urls") == [], "entity must NOT be mutated in shadow mode"
    assert repo.get("_test_coverage") is None


# --- off mode ---


def test_off_never_calls_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V2_REPO_SIGNALS_AGENT_MODE", "off")
    ran: list[int] = []

    async def _stub_runner(inp: Any, **_kw: Any) -> _FixedPatch:
        ran.append(1)
        return _FIXED_PATCH

    monkeypatch.setattr(
        "git_metadata_extractor.pipeline.stages.refine_with_llm.run_repo_signals",
        _stub_runner,
    )

    repo = _make_repo()
    _run(_run_repo_signals_pass(repositories=[repo], gathered_context=_gathered(repo)))
    assert not ran, "runner must NOT be called in off mode"
    assert repo.get("_documentation_urls") == []


def test_default_mode_is_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("V2_REPO_SIGNALS_AGENT_MODE", raising=False)
    ran: list[int] = []

    async def _stub_runner(inp: Any, **_kw: Any) -> _FixedPatch:
        ran.append(1)
        return _FIXED_PATCH

    monkeypatch.setattr(
        "git_metadata_extractor.pipeline.stages.refine_with_llm.run_repo_signals",
        _stub_runner,
    )

    repo = _make_repo()
    _run(_run_repo_signals_pass(repositories=[repo], gathered_context=_gathered(repo)))
    assert ran, "default mode is apply — runner must be called"
    assert repo.get("_documentation_urls") == _DOC_URLS


# --- no README ---


def test_no_readme_skips_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V2_REPO_SIGNALS_AGENT_MODE", "apply")
    ran: list[int] = []

    async def _stub_runner(inp: Any, **_kw: Any) -> _FixedPatch:
        ran.append(1)
        return _FIXED_PATCH

    monkeypatch.setattr(
        "git_metadata_extractor.pipeline.stages.refine_with_llm.run_repo_signals",
        _stub_runner,
    )

    repo = _make_repo(readme=None)
    # gathered_context has no readme
    _run(
        _run_repo_signals_pass(
            repositories=[repo],
            gathered_context={"repository": {"readme_content": None}},
        ),
    )
    assert not ran, "agent must be skipped when there is no README"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _gathered(repo: dict[str, Any]) -> dict[str, Any]:
    """Build a minimal gathered_context that mirrors refine_with_llm's shape."""
    readme = repo.get("_readme_content")
    return {
        "repository": {
            "readme_content": readme,
            "metadata": {},
        },
    }
