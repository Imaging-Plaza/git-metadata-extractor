"""`extract_gimie` must hand gimie a single PAT, not a comma-separated pool.

Regression test for the rename-PR follow-up: `gimie.GithubExtractor`
reads `os.environ["GITHUB_TOKEN"]` verbatim and 401s on a pool string.
The wrapper in `src/v1/gimie_utils/gimie_methods.py` normalises that
env before calling `Project(...)` and restores it on exit, so callers
(production runner + standalone tests) don't need a per-site shim.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from src.v1.gimie_utils import gimie_methods


class _StubGraph:
    def serialize(self, format: str = "json-ld") -> str:
        return "[]"


class _StubProject:
    """Captures the value of `os.environ['GITHUB_TOKEN']` at the moment
    gimie would have read it inside the context manager.
    """

    captured_github_token: str | None = None

    def __init__(self, full_path: str) -> None:
        type(self).captured_github_token = os.environ.get("GITHUB_TOKEN")

    def extract(self) -> _StubGraph:
        return _StubGraph()


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GME_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GME_GITHUB_TOKEN_POOL", raising=False)
    _StubProject.captured_github_token = None


def test_pool_first_token_promoted_to_legacy_env(monkeypatch):
    monkeypatch.setenv("GME_GITHUB_TOKEN_POOL", "ghp_pool_a,ghp_pool_b,ghp_pool_c")
    monkeypatch.setenv("GME_GITHUB_TOKEN", "ghp_pool_a")

    with patch.object(gimie_methods, "Project", _StubProject):
        gimie_methods.extract_gimie("https://github.com/owner/repo")

    assert _StubProject.captured_github_token == "ghp_pool_a"


def test_gme_single_used_when_pool_unset(monkeypatch):
    monkeypatch.setenv("GME_GITHUB_TOKEN", "ghp_solo")

    with patch.object(gimie_methods, "Project", _StubProject):
        gimie_methods.extract_gimie("https://github.com/owner/repo")

    assert _StubProject.captured_github_token == "ghp_solo"


def test_legacy_multi_comma_value_collapsed_to_first(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_legacy_a,ghp_legacy_b")

    with patch.object(gimie_methods, "Project", _StubProject):
        gimie_methods.extract_gimie("https://github.com/owner/repo")

    assert _StubProject.captured_github_token == "ghp_legacy_a"
    # And restored to the literal pool string after the call returns.
    assert os.environ["GITHUB_TOKEN"] == "ghp_legacy_a,ghp_legacy_b"


def test_prior_legacy_value_restored_on_success(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_prior_legacy")
    monkeypatch.setenv("GME_GITHUB_TOKEN_POOL", "ghp_pool_a,ghp_pool_b")

    with patch.object(gimie_methods, "Project", _StubProject):
        gimie_methods.extract_gimie("https://github.com/owner/repo")

    assert _StubProject.captured_github_token == "ghp_pool_a"
    assert os.environ["GITHUB_TOKEN"] == "ghp_prior_legacy"


def test_prior_unset_left_unset_on_success(monkeypatch):
    monkeypatch.setenv("GME_GITHUB_TOKEN_POOL", "ghp_pool_a")

    with patch.object(gimie_methods, "Project", _StubProject):
        gimie_methods.extract_gimie("https://github.com/owner/repo")

    assert _StubProject.captured_github_token == "ghp_pool_a"
    assert "GITHUB_TOKEN" not in os.environ


def test_no_tokens_anywhere_leaves_env_untouched(monkeypatch):
    with patch.object(gimie_methods, "Project", _StubProject):
        gimie_methods.extract_gimie("https://github.com/owner/repo")

    assert _StubProject.captured_github_token is None
    assert "GITHUB_TOKEN" not in os.environ


def test_env_restored_when_gimie_raises(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_prior_legacy")
    monkeypatch.setenv("GME_GITHUB_TOKEN_POOL", "ghp_pool_a,ghp_pool_b")

    class _BoomProject:
        def __init__(self, full_path: str) -> None:
            pass

        def extract(self):
            raise RuntimeError("gimie blew up")

    with patch.object(gimie_methods, "Project", _BoomProject):
        with pytest.raises(RuntimeError, match="gimie blew up"):
            gimie_methods.extract_gimie("https://github.com/owner/repo")

    assert os.environ["GITHUB_TOKEN"] == "ghp_prior_legacy"
