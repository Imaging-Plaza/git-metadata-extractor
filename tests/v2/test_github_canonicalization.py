"""Tests for the shared GitHub canonical-URL helpers."""

from __future__ import annotations

import pytest

from src.v2.canonicalization.github import (
    github_org_iri,
    github_repo_iri,
    github_user_iri,
    parse_github_org_iri,
    parse_github_repo_iri,
    parse_github_user_iri,
)


# ---------------------------------------------------------------------------
# User / Org happy path (same URL shape)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "caviri",
        "  caviri  ",
        "@caviri",
        "https://github.com/caviri",
        "https://github.com/caviri/",
        "HTTPS://GITHUB.COM/caviri",   # case-insensitive scheme/host
    ],
)
def test_github_user_iri_canonicalises_every_input_shape(raw):
    assert github_user_iri(raw) == "https://github.com/caviri"


def test_github_org_iri_is_alias_of_user():
    """Org URLs are identical in shape to user URLs — the two helpers
    differ in name only, for caller-side readability."""
    assert github_org_iri("Imaging-Plaza") == "https://github.com/Imaging-Plaza"
    assert github_org_iri("Imaging-Plaza") == github_user_iri("Imaging-Plaza")


def test_user_iri_is_idempotent():
    canonical = "https://github.com/caviri"
    assert github_user_iri(canonical) == canonical


# ---------------------------------------------------------------------------
# User / Org rejection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "caviri/some-repo",                 # contains slash — looks like a repo
        "https://github.com/owner/repo",    # URL is a repo, not a user
        "-caviri",                          # leading hyphen
        "caviri-",                          # trailing hyphen
        "ca--viri",                         # double hyphen
        "x" * 40,                           # too long (max 39)
        "carl@epfl.ch",                     # contains `@` mid-string (not as leading)
        "https://gitlab.com/caviri",        # wrong host
        "caviri ros",                       # contains space
    ],
)
def test_user_iri_returns_none_for_malformed_input(raw):
    assert github_user_iri(raw) is None


# ---------------------------------------------------------------------------
# Repository happy path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "Imaging-Plaza/git-metadata-extractor",
        "  Imaging-Plaza/git-metadata-extractor  ",
        "@Imaging-Plaza/git-metadata-extractor",
        "https://github.com/Imaging-Plaza/git-metadata-extractor",
        "https://github.com/Imaging-Plaza/git-metadata-extractor/",
    ],
)
def test_github_repo_iri_canonicalises_every_input_shape(raw):
    assert github_repo_iri(raw) == (
        "https://github.com/Imaging-Plaza/git-metadata-extractor"
    )


def test_repo_iri_accepts_dotted_repo_names():
    assert github_repo_iri("user/my.project") == "https://github.com/user/my.project"
    # Leading dot is allowed in repo names.
    assert github_repo_iri("user/.dotfiles") == "https://github.com/user/.dotfiles"


def test_repo_iri_is_idempotent():
    canonical = "https://github.com/owner/repo"
    assert github_repo_iri(canonical) == canonical


# ---------------------------------------------------------------------------
# Repository rejection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "owner",                            # missing repo half
        "owner/",                           # empty repo half
        "/repo",                            # empty owner half
        "owner/repo/extra",                 # too many segments
        "https://github.com/owner",         # URL missing repo half
        "https://gitlab.com/owner/repo",    # wrong host
        "-owner/repo",                      # bad owner shape
        "owner/repo with spaces",           # invalid repo name
    ],
)
def test_repo_iri_returns_none_for_malformed_input(raw):
    assert github_repo_iri(raw) is None


# ---------------------------------------------------------------------------
# parse_* (inverses)
# ---------------------------------------------------------------------------


def test_parse_user_iri_returns_handle():
    assert parse_github_user_iri("https://github.com/caviri") == "caviri"
    assert parse_github_user_iri("caviri") == "caviri"  # idempotent on bare


def test_parse_org_iri_is_alias_of_user():
    assert parse_github_org_iri("Imaging-Plaza") == "Imaging-Plaza"
    assert parse_github_org_iri(
        "https://github.com/Imaging-Plaza",
    ) == "Imaging-Plaza"


def test_parse_repo_iri_returns_owner_repo_tuple():
    assert parse_github_repo_iri(
        "https://github.com/Imaging-Plaza/git-metadata-extractor",
    ) == ("Imaging-Plaza", "git-metadata-extractor")
    assert parse_github_repo_iri(
        "owner/repo",
    ) == ("owner", "repo")


def test_parse_returns_none_on_garbage():
    assert parse_github_user_iri(None) is None
    assert parse_github_user_iri("not a handle") is None
    assert parse_github_repo_iri("not/a/valid/repo") is None
