# tests/v2/test_auto_ingest_env_alias.py
"""Bug 06: the documented flag V2_GITHUB_RAG_AUTO_INGEST never matched the name
the code reads (V2_GITHUB_REPOS_RAG_AUTO_INGEST), so operators who followed the
docs silently got no auto-ingest. _auto_ingest_enabled accepts the old name as a
deprecated alias (with a warning) and lets the canonical name win: once the
canonical name is set, its value alone decides, so an explicit "false" is not
overridden by a leftover alias.
"""
from __future__ import annotations

import logging

import pytest

from git_metadata_extractor.api._helpers import _auto_ingest_enabled

CANON = "V2_GITHUB_REPOS_RAG_AUTO_INGEST"
ALIAS = "V2_GITHUB_RAG_AUTO_INGEST"


# (canonical value, alias value) -> (enabled, deprecation warning logged);
# None means the variable is unset.
@pytest.mark.parametrize(
    ("env", "outcome"),
    [
        pytest.param((None, None), (False, False), id="neither-set"),
        pytest.param(("true", None), (True, False), id="canonical-enables"),
        pytest.param((None, "true"), (True, True), id="alias-enables-with-warning"),
        pytest.param(("true", "true"), (True, False), id="canonical-wins-no-warning"),
        pytest.param(("false", None), (False, False), id="canonical-false"),
        pytest.param(("false", "true"), (False, False), id="canonical-false-beats-alias"),
    ],
)
def test_auto_ingest_flag_resolution(monkeypatch, caplog, env, outcome):
    for name, value in zip((CANON, ALIAS), env, strict=True):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    with caplog.at_level(logging.WARNING):
        enabled = _auto_ingest_enabled(CANON, ALIAS)
    warned = any("deprecated env var" in r.getMessage() for r in caplog.records)
    assert (enabled, warned) == outcome
