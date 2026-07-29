"""Regression for #51 / #55: the auto-ingest closure built `GitHubClient(cfg)`
positionally, but `GitHubClient.__init__` is keyword-only — so every
`V2_GITHUB_REPOS_RAG_AUTO_INGEST=true` deployment was silently 100%-failing the
background ingest with a `TypeError` (catalogs stuck since 2026-05-13 / -24).

The minimal guard: schedule the closure end-to-end with every heavy dep
patched out, and assert it does **not** log the `failed` line and that
`GitHubClient` was called with the right kwargs.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import types
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from git_metadata_extractor.api import auto_ingest as v2_api


@pytest.fixture
def fake_classification() -> Any:
    """Stand-in for the GitHubURLClassification the closure inspects."""
    cls = types.SimpleNamespace(
        detected_type=types.SimpleNamespace(value="repository"),
        normalized_url="https://github.com/octocat/Hello-World",
    )
    return cls


@pytest.fixture
def fake_config() -> Any:
    """A config double exposing only what the auto-ingest closure reads."""
    return types.SimpleNamespace(
        require_github=lambda: None,
        github=types.SimpleNamespace(
            api_base="https://api.github.com",
            token="ghp_test_token",
        ),
        paths=types.SimpleNamespace(
            duckdb_path=Path("/tmp/not-real.duckdb"),
            cache_db_path=Path("/tmp/not-real-providers.db"),
        ),
    )


class _GitHubClientSpy:
    """Records kwargs from each construction."""

    last_kwargs: dict[str, Any] | None = None

    def __init__(self, **kwargs: Any) -> None:
        type(self).last_kwargs = kwargs

    @classmethod
    def reset(cls) -> None:
        cls.last_kwargs = None


class _GitHubStoreStub:
    @classmethod
    def open(cls, _path: Path) -> "_GitHubStoreStub":
        return cls()

    def fetch_repo(self, _full_name: str) -> None:
        return None  # forces the closure into the GitHubClient(...) branch

    def close(self) -> None:
        return None


@contextmanager
def _patched_ingest_modules(monkeypatch, *, config: Any, client_cls: type) -> Any:
    """Stub every module the closure imports lazily.

    The closure does `from open_pulse_sources.index.github_repos.* import ...` inside `_run`, so
    inserting fake modules into `sys.modules` before the closure runs is
    enough to intercept those imports without monkeypatching attributes
    on objects the closure never touches.
    """

    def _ingest_single_repo(**_kwargs: Any) -> str:
        return "ingested"

    def _embed_repos(**_kwargs: Any) -> dict[str, int]:
        return {"repos": 1}

    fakes = {
        "open_pulse_sources.index.github_repos.config": types.SimpleNamespace(
            load_config=lambda: config,
        ),
        "open_pulse_sources.index.github_repos.embed.pipeline": types.SimpleNamespace(
            embed_repos=_embed_repos,
        ),
        "open_pulse_sources.index.github_repos.ingest.github_client": types.SimpleNamespace(
            GitHubClient=client_cls,
        ),
        "open_pulse_sources.index.github_repos.ingest.repos": types.SimpleNamespace(
            ingest_single_repo=_ingest_single_repo,
        ),
        "open_pulse_sources.index.github_repos.storage.duckdb_store": types.SimpleNamespace(
            GitHubReposStore=_GitHubStoreStub,
        ),
    }
    originals = {name: sys.modules.get(name) for name in fakes}
    for name, mod in fakes.items():
        sys.modules[name] = mod
    try:
        yield
    finally:
        for name, original in originals.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


def test_auto_ingest_constructs_github_client_with_keyword_args(
    monkeypatch,
    caplog,
    fake_classification,
    fake_config,
):
    monkeypatch.setenv("V2_GITHUB_REPOS_RAG_AUTO_INGEST", "true")
    _GitHubClientSpy.reset()

    with _patched_ingest_modules(
        monkeypatch, config=fake_config, client_cls=_GitHubClientSpy,
    ):
        async def _drive() -> None:
            with caplog.at_level(logging.INFO, logger="git_metadata_extractor.api"):
                v2_api._maybe_schedule_github_repos_auto_ingest(
                    classification=fake_classification, run_id="test-run",
                )
                # Yield once so the asyncio.create_task() inside has a
                # chance to schedule the inner `_do_ingest` thread. We then
                # await the thread executor to drain.
                await asyncio.sleep(0)
                # Give the to_thread executor time to finish.
                for _ in range(20):
                    if _GitHubClientSpy.last_kwargs is not None:
                        break
                    await asyncio.sleep(0.05)

        asyncio.run(_drive())

    assert _GitHubClientSpy.last_kwargs == {
        "api_base": "https://api.github.com",
        "token": "ghp_test_token",
        "cache_path": Path("/tmp/not-real-providers.db"),
    }
    # And nothing logged the regression signature.
    assert not any(
        "auto-ingest" in record.message and "failed" in record.message
        for record in caplog.records
    )


def test_auto_ingest_is_no_op_when_env_disabled(
    monkeypatch, fake_classification, fake_config,
):
    monkeypatch.delenv("V2_GITHUB_REPOS_RAG_AUTO_INGEST", raising=False)
    _GitHubClientSpy.reset()
    with _patched_ingest_modules(
        monkeypatch, config=fake_config, client_cls=_GitHubClientSpy,
    ):
        v2_api._maybe_schedule_github_repos_auto_ingest(
            classification=fake_classification, run_id="test-run",
        )
    assert _GitHubClientSpy.last_kwargs is None
