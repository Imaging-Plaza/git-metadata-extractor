# tests/v2/test_gitlab_canonicalization.py
"""Tests for the GitLab canonical-URL helpers."""
from __future__ import annotations

import pytest

from git_metadata_extractor.canonicalization.gitlab import gitlab_iri, parse_gitlab_iri

HOST = "gitlab.epfl.ch"


def test_iri_project_from_path():
    assert gitlab_iri(HOST, "project", "group/sub/proj") == "https://gitlab.epfl.ch/group/sub/proj"


def test_iri_group_uses_groups_segment():
    assert gitlab_iri(HOST, "group", "group/sub") == "https://gitlab.epfl.ch/groups/group/sub"


def test_iri_user():
    assert gitlab_iri(HOST, "user", "alice") == "https://gitlab.epfl.ch/alice"


def test_iri_idempotent_on_url_and_trailing_slash():
    url = "https://gitlab.epfl.ch/group/proj"
    assert gitlab_iri(HOST, "project", url) == url
    assert gitlab_iri(HOST, "project", url + "/") == url
    assert gitlab_iri(HOST, "project", "  group/proj  ") == url


@pytest.mark.parametrize("bad", [None, "", "   ", 42, "https://example.com/x"])
def test_iri_rejects_garbage(bad):
    assert gitlab_iri(HOST, "project", bad) is None


def test_iri_rejects_unknown_kind():
    with pytest.raises(ValueError, match="kind"):
        gitlab_iri(HOST, "spaceship", "x")


def test_parse_round_trips():
    assert parse_gitlab_iri("https://gitlab.epfl.ch/group/proj") == ("gitlab.epfl.ch", "project", "group/proj")
    assert parse_gitlab_iri("https://gitlab.epfl.ch/groups/group/sub") == ("gitlab.epfl.ch", "group", "group/sub")
    assert parse_gitlab_iri("https://gitlab.epfl.ch/alice") == ("gitlab.epfl.ch", "user", "alice")


def test_parse_returns_none_on_non_gitlab():
    assert parse_gitlab_iri(None) is None
    assert parse_gitlab_iri("group/proj") is None  # bare path is not a URL
