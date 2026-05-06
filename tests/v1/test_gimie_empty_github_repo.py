"""Regression: GIMIE on GitHub repos with no commits (204 contributors, null HEAD tree)."""

from __future__ import annotations

import os

import pytest

from src.v1.gimie_utils.gimie_methods import extract_gimie


@pytest.mark.skipif(
    not os.environ.get("GITHUB_TOKEN"),
    reason="GITHUB_TOKEN required for live GitHub API",
)
def test_extract_gimie_empty_github_repository_returns_json_ld_list():
    """raj921/dream-home-ai- is an empty GitHub repo (API regression fixture)."""
    out = extract_gimie(
        "https://github.com/raj921/dream-home-ai-",
        serialization_format="json-ld",
    )
    assert isinstance(out, list)
