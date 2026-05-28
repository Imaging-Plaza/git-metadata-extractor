"""Tests for the shared `orcid_iri` / `parse_orcid` helpers.

The pipeline previously had 10 separate `_normalize_orcid`
implementations with subtly different behaviour (uppercase vs not,
validate vs not, return-bare vs return-URL). This helper collapses
them to one consistent contract:

  - Input: any reasonable shape (bare, URL, `orcid:`-prefixed,
    legacy `http://orcid.org/`, trailing slash, whitespace, lowercase
    `x` checksum).
  - Output: canonical `https://orcid.org/<BARE-UPPERCASE>` or None.
  - Idempotent on canonical input.
  - Does NOT validate the mod-11 checksum (provider concern).
"""

from __future__ import annotations

import pytest

from src.v2.canonicalization.orcid import (
    ORCID_BARE_RE,
    orcid_iri,
    parse_orcid,
)


# ---------------------------------------------------------------------------
# Happy path — every accepted input shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "0000-0001-2345-6789",
        "  0000-0001-2345-6789  ",                # whitespace
        "https://orcid.org/0000-0001-2345-6789",
        "https://orcid.org/0000-0001-2345-6789/", # trailing slash
        "http://orcid.org/0000-0001-2345-6789",   # legacy http
        "orcid:0000-0001-2345-6789",              # scheme prefix
        "ORCID:0000-0001-2345-6789",              # uppercase scheme
        "HTTPS://ORCID.ORG/0000-0001-2345-6789",  # uppercase host
    ],
)
def test_orcid_iri_canonicalises_every_known_input_shape(raw):
    assert orcid_iri(raw) == "https://orcid.org/0000-0001-2345-6789"


def test_orcid_iri_uppercases_lowercase_checksum():
    """The mod-11 checksum char is `0-9` or `X` (uppercase). Some
    sources emit lowercase `x`; canonical form requires `X`."""
    assert orcid_iri("0000-0002-1825-009x") == "https://orcid.org/0000-0002-1825-009X"
    assert orcid_iri("https://orcid.org/0000-0002-1825-009x") == (
        "https://orcid.org/0000-0002-1825-009X"
    )


def test_orcid_iri_is_idempotent():
    canonical = "https://orcid.org/0000-0001-2345-6789"
    assert orcid_iri(canonical) == canonical
    # Two-pass also idempotent.
    assert orcid_iri(orcid_iri(canonical)) == canonical


# ---------------------------------------------------------------------------
# Rejection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "not-an-orcid",
        "0000-0001-2345",          # too few groups
        "0000-0001-2345-6789-abc", # too many groups
        "0000-0001-2345-67890",    # last group too long
        "0000-0001-2345-678",      # last group too short
        "0000-0001-2345-XXXX",     # non-digit in non-checksum position
        "https://example.com/0000-0001-2345-6789",  # wrong host
    ],
)
def test_orcid_iri_returns_none_for_malformed_input(raw):
    assert orcid_iri(raw) is None


def test_orcid_iri_returns_none_for_non_string_input():
    assert orcid_iri(123) is None  # type: ignore[arg-type]
    assert orcid_iri(["0000-0001-2345-6789"]) is None  # type: ignore[arg-type]
    assert orcid_iri({"orcid": "0000-0001-2345-6789"}) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# parse_orcid (inverse) — returns bare form for catalog-backend storage
# ---------------------------------------------------------------------------


def test_parse_orcid_extracts_bare_form_from_url():
    assert parse_orcid("https://orcid.org/0000-0001-2345-6789") == "0000-0001-2345-6789"


def test_parse_orcid_handles_bare_input_idempotently():
    assert parse_orcid("0000-0001-2345-6789") == "0000-0001-2345-6789"


def test_parse_orcid_uppercases_checksum():
    assert parse_orcid("0000-0002-1825-009x") == "0000-0002-1825-009X"


def test_parse_orcid_returns_none_on_garbage():
    assert parse_orcid(None) is None
    assert parse_orcid("garbage") is None


# ---------------------------------------------------------------------------
# Regex export (callers that want to type-check on top)
# ---------------------------------------------------------------------------


def test_bare_regex_accepts_canonical_form():
    assert ORCID_BARE_RE.fullmatch("0000-0001-2345-6789")
    assert ORCID_BARE_RE.fullmatch("0000-0002-1825-009X")


def test_bare_regex_rejects_lowercase_checksum_x():
    # Caller's responsibility to upper-case before checking;
    # `orcid_iri` does this for you, the raw regex doesn't.
    assert not ORCID_BARE_RE.fullmatch("0000-0002-1825-009x")
