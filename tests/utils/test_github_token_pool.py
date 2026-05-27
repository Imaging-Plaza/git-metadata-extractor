"""Tests for the shared GitHub token-pool helper."""

from __future__ import annotations

import pytest

from src.utils import github_token_pool


@pytest.fixture(autouse=True)
def _reset_cycle(monkeypatch):
    """Force each test to start from a clean per-process state."""
    monkeypatch.setattr(github_token_pool, "_CYCLE", None)
    monkeypatch.setattr(github_token_pool, "_SOURCE", None)
    monkeypatch.delenv("GME_GITHUB_TOKEN_POOL", raising=False)
    monkeypatch.delenv("GME_GITHUB_TOKEN", raising=False)


def test_returns_empty_when_no_token_configured():
    assert github_token_pool.next_github_token() == ""


def test_single_token_repeats(monkeypatch):
    monkeypatch.setenv("GME_GITHUB_TOKEN", "ghp_solo")
    assert github_token_pool.next_github_token() == "ghp_solo"
    assert github_token_pool.next_github_token() == "ghp_solo"


def test_pool_rotates_round_robin(monkeypatch):
    monkeypatch.setenv("GME_GITHUB_TOKEN_POOL", "ghp_a,ghp_b,ghp_c")
    seq = [github_token_pool.next_github_token() for _ in range(7)]
    assert seq == ["ghp_a", "ghp_b", "ghp_c", "ghp_a", "ghp_b", "ghp_c", "ghp_a"]


def test_pool_preferred_over_single_token(monkeypatch):
    monkeypatch.setenv("GME_GITHUB_TOKEN_POOL", "ghp_pool_a,ghp_pool_b")
    monkeypatch.setenv("GME_GITHUB_TOKEN", "ghp_single")
    seq = [github_token_pool.next_github_token() for _ in range(4)]
    assert seq == ["ghp_pool_a", "ghp_pool_b", "ghp_pool_a", "ghp_pool_b"]


def test_cycle_resets_when_env_changes(monkeypatch):
    monkeypatch.setenv("GME_GITHUB_TOKEN_POOL", "ghp_a,ghp_b")
    assert github_token_pool.next_github_token() == "ghp_a"
    monkeypatch.setenv("GME_GITHUB_TOKEN_POOL", "ghp_x,ghp_y,ghp_z")
    seq = [github_token_pool.next_github_token() for _ in range(3)]
    assert seq == ["ghp_x", "ghp_y", "ghp_z"]


def test_whitespace_and_empty_tokens_ignored(monkeypatch):
    monkeypatch.setenv("GME_GITHUB_TOKEN_POOL", " ghp_a , , ghp_b ,")
    seq = [github_token_pool.next_github_token() for _ in range(4)]
    assert seq == ["ghp_a", "ghp_b", "ghp_a", "ghp_b"]


def test_auth_headers_include_rotated_token(monkeypatch):
    monkeypatch.setenv("GME_GITHUB_TOKEN_POOL", "ghp_a,ghp_b")
    h1 = github_token_pool.github_auth_headers()
    h2 = github_token_pool.github_auth_headers()
    assert h1["Authorization"] == "token ghp_a"
    assert h2["Authorization"] == "token ghp_b"
    assert h1["Accept"] == "application/vnd.github+json"


def test_auth_headers_merge_extra_without_mutation(monkeypatch):
    monkeypatch.setenv("GME_GITHUB_TOKEN", "ghp_solo")
    extra = {"User-Agent": "TestAgent/1.0", "Accept": "application/vnd.github.v3+json"}
    headers = github_token_pool.github_auth_headers(extra=extra)
    assert headers["User-Agent"] == "TestAgent/1.0"
    # Caller-provided Accept overrides the default.
    assert headers["Accept"] == "application/vnd.github.v3+json"
    assert headers["Authorization"] == "token ghp_solo"
    # `extra` is not mutated.
    assert "Authorization" not in extra


def test_auth_headers_omit_authorization_when_no_token():
    headers = github_token_pool.github_auth_headers()
    assert "Authorization" not in headers
    assert headers["Accept"] == "application/vnd.github+json"
