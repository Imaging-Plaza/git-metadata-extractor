"""v1 endpoints must reject non-github URLs before invoking the LLM pipeline.

Issue #11 / #12: the atomic-agent pipeline behind every v1 user/org/repo
route hard-wires GitHub's REST + GraphQL schemas, so feeding a
`gitlab.epfl.ch` URL through them produces hallucinated
`github.com/unknown/<repo>` outputs (images, README links, executable
instructions). GitLab support proper is tracked in #54; until that lands
the guard in `src.api._assert_github_host` short-circuits the request
with a clear 422.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from src.api import _assert_github_host


GITHUB_INPUTS = [
    "https://github.com/sdsc-ordes/gimie",
    "http://github.com/sdsc-ordes/gimie",
    "github.com/sdsc-ordes/gimie",
    "https://www.github.com/sdsc-ordes/gimie",
    "https://github.com/octocat",
    "https://github.com/orgs/Imaging-Plaza",
]

NON_GITHUB_INPUTS = [
    "https://gitlab.epfl.ch/lasa/FlowMRI-Net",
    "https://gitlab.ethz.ch/foo/bar",
    "https://gitlab.renkulab.io/some/project",
    "https://gitlab.com/group/project",
    "https://bitbucket.org/team/repo",
    "https://example.com/owner/repo",
    "gitlab.epfl.ch/lasa/FlowMRI-Net",  # no scheme — still rejected
]


@pytest.mark.parametrize("url", GITHUB_INPUTS)
def test_assert_github_host_accepts_github_inputs(url: str) -> None:
    """All accepted shapes — with/without scheme, with/without `www`."""
    # Must not raise.
    _assert_github_host(url)


@pytest.mark.parametrize("url", NON_GITHUB_INPUTS)
def test_assert_github_host_rejects_non_github_inputs(url: str) -> None:
    with pytest.raises(HTTPException) as excinfo:
        _assert_github_host(url)
    err = excinfo.value
    assert err.status_code == 422
    # The detail must surface the host (or the raw input as a fallback) so
    # the caller can see *what* was rejected, plus point at the right
    # follow-up issue.
    assert "v1 endpoints only accept github.com" in err.detail
    assert "#54" in err.detail


def test_assert_github_host_rejects_empty_input() -> None:
    """Empty / whitespace-only paths produce a 422 instead of TypeError."""
    with pytest.raises(HTTPException) as excinfo:
        _assert_github_host("")
    assert excinfo.value.status_code == 422


def test_assert_github_host_detail_mentions_v2_alternative() -> None:
    """Operators should know v2 already gives a clean 422 — point them there."""
    with pytest.raises(HTTPException) as excinfo:
        _assert_github_host("https://gitlab.epfl.ch/lasa/FlowMRI-Net")
    assert "/v2/extract" in excinfo.value.detail
