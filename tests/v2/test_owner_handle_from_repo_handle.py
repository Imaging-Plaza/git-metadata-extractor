"""Regression tests for the owner-handle parser.

The URL form used to yield the scheme fragment `'https:'`, which cascaded into
46 SHACL violations across the 120-URL baseline corpus — see
`tests/v2/corpus/BASELINE_FINDINGS.md` for the full chain.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from git_metadata_extractor.pipeline.stages.ownership_check import (
    _extract_owner_from_repo_handle,
)


@pytest.mark.parametrize(
    ("handle", "expected"),
    [
        # The form 90 of 95 corpus repositories actually carry.
        ("https://github.com/LIONS-EPFL/ELLE", "LIONS-EPFL"),
        ("http://github.com/LIONS-EPFL/ELLE", "LIONS-EPFL"),
        ("https://www.github.com/LIONS-EPFL/ELLE", "LIONS-EPFL"),
        # Case in the prefix must not affect the preserved owner casing.
        ("HTTPS://GitHub.com/LIONS-EPFL/ELLE", "LIONS-EPFL"),
        # The bare form the other 5 carry.
        ("HippolyteKarakostas/galaxy2galaxy", "HippolyteKarakostas"),
        # Surrounding whitespace and trailing path noise.
        ("  https://github.com/octocat/hello  ", "octocat"),
        ("https://github.com/octocat/hello/tree/main", "octocat"),
        ("https://github.com//octocat/hello", "octocat"),
    ],
)
def test_extracts_owner_from_both_handle_forms(handle: str, expected: str) -> None:
    assert _extract_owner_from_repo_handle(handle) == expected


@pytest.mark.parametrize(
    "handle",
    [
        # Owner-only URL: no repo segment, so not a repository handle.
        "https://github.com/octocat",
        "https://github.com/octocat/",
        # A bare login is not `<owner>/<repo>` either.
        "octocat",
        "",
        "   ",
        None,
        123,
        {"@id": "https://github.com/octocat/hello"},
    ],
)
def test_returns_none_when_there_is_no_owner_repo_pair(handle: object) -> None:
    assert _extract_owner_from_repo_handle(handle) is None


def test_never_returns_a_scheme_fragment() -> None:
    """The specific regression: a URL must not parse as the handle `https:`."""
    for handle in (
        "https://github.com/a/b",
        "http://github.com/a/b",
        "https://gitlab.com/a/b",
        "https://example.org/a/b",
    ):
        assert _extract_owner_from_repo_handle(handle) != "https:"
        assert _extract_owner_from_repo_handle(handle) != "http:"


def test_url_path_is_parsed_for_any_host() -> None:
    """Host-agnostic on purpose — GitLab handles are coming in phase 4.

    Parsing the path rather than stripping a known github prefix means a
    non-GitHub URL also yields its first path segment instead of `'https:'`.
    """
    assert _extract_owner_from_repo_handle("https://gitlab.com/a/b") == "a"
    assert _extract_owner_from_repo_handle("https://gitlab.epfl.ch/grp/sub/proj") == "grp"


# --------------------------------------------------------------------------
# `pulse:githubUsername` is a URL on every synthesized Person stub
# --------------------------------------------------------------------------

GITHUB_USERNAME_PATTERN = re.compile(
    r"^https://github\.com/[A-Za-z0-9][A-Za-z0-9-]{0,38}$",
)


def _person_stubs(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in entities if e.get("type") == "schema:Person"]


def test_fork_parent_owner_stub_uses_the_canonical_profile_url() -> None:
    """`ownership_check` has three Person-stub producers; all three had this bug.

    Both the agent and strict JSON Schemas, and the SHACL PersonShape, pattern
    `pulse:githubUsername` against `^https://github\\.com/...`. The bare handle
    was written by the fork-parent emitter, which produced a SHACL violation on
    5 of 120 corpus runs — every repo whose fork parent's owner was not already
    in the graph. `schema:name` keeps the bare handle: that one is a display
    string.
    """
    from git_metadata_extractor.pipeline.stages.models import (  # noqa: PLC0415
        AssembledOutput,
    )
    from git_metadata_extractor.pipeline.stages.ownership_check import (  # noqa: PLC0415
        emit_fork_parent_stubs,
    )

    repo = {
        "id": "https://github.com/d3b-center/OpenPedCan-analysis",
        "type": "schema:SoftwareSourceCode",
        "pulse:isForkOf": {
            "@id": "https://github.com/AlexsLemonade/OpenPBTA-analysis",
        },
    }
    assembled, _warnings = emit_fork_parent_stubs(
        AssembledOutput(root_entity=repo, related_entities=[], excluded_entities=[]),
    )

    stubs = _person_stubs(assembled.related_entities)
    assert stubs, "no Person stub emitted for the fork parent's owner"
    for stub in stubs:
        handle = stub["pulse:githubUsername"]
        assert GITHUB_USERNAME_PATTERN.match(handle), (
            f"pulse:githubUsername must be a canonical profile URL, got {handle!r}"
        )
        assert stub["identifiers"]["pulse:githubUsername"] == handle
        # The display name stays bare.
        assert stub["schema:name"] == "AlexsLemonade"
