# tests/v2/test_auto_ingest_env_alias.py
"""Bug 06: the documented flag V2_GITHUB_RAG_AUTO_INGEST never matched the name
the code reads (V2_GITHUB_REPOS_RAG_AUTO_INGEST), so operators who followed the
docs silently got no auto-ingest. _auto_ingest_enabled accepts the old name as a
deprecated alias (with a warning) and lets the canonical name win.
"""
from __future__ import annotations

import logging

from git_metadata_extractor.api import _auto_ingest_enabled

CANON = "V2_GITHUB_REPOS_RAG_AUTO_INGEST"
ALIAS = "V2_GITHUB_RAG_AUTO_INGEST"


def test_disabled_when_neither_set(monkeypatch):
    monkeypatch.delenv(CANON, raising=False)
    monkeypatch.delenv(ALIAS, raising=False)
    assert _auto_ingest_enabled(CANON, ALIAS) is False


def test_canonical_enables_without_warning(monkeypatch, caplog):
    monkeypatch.delenv(ALIAS, raising=False)
    monkeypatch.setenv(CANON, "true")
    with caplog.at_level(logging.WARNING):
        assert _auto_ingest_enabled(CANON, ALIAS) is True
    assert not any("deprecated" in r.getMessage() for r in caplog.records)


def test_alias_enables_with_deprecation_warning(monkeypatch, caplog):
    monkeypatch.delenv(CANON, raising=False)
    monkeypatch.setenv(ALIAS, "true")
    with caplog.at_level(logging.WARNING):
        assert _auto_ingest_enabled(CANON, ALIAS) is True
    assert any("deprecated env var" in r.getMessage() for r in caplog.records)


def test_canonical_takes_precedence_without_warning(monkeypatch, caplog):
    monkeypatch.setenv(CANON, "true")
    monkeypatch.setenv(ALIAS, "true")
    with caplog.at_level(logging.WARNING):
        assert _auto_ingest_enabled(CANON, ALIAS) is True
    assert not any("deprecated" in r.getMessage() for r in caplog.records)


def test_false_value_disables(monkeypatch):
    monkeypatch.setenv(CANON, "false")
    monkeypatch.delenv(ALIAS, raising=False)
    assert _auto_ingest_enabled(CANON, ALIAS) is False
