"""Tests for the SNSF grant canonical-URL helpers."""

from __future__ import annotations

import pytest

from git_metadata_extractor.canonicalization.snsf import (
    parse_snsf_grant,
    snsf_grant_iri,
    snsf_grant_point_id,
)

_URL = "https://data.snf.ch/grants/grant/241892"


def test_iri_from_int_and_str():
    assert snsf_grant_iri(241892) == _URL
    assert snsf_grant_iri("241892") == _URL
    assert snsf_grant_iri("  241892  ") == _URL


def test_iri_idempotent_on_url():
    assert snsf_grant_iri(_URL) == _URL
    assert snsf_grant_iri(_URL + "/") == _URL
    assert snsf_grant_iri("http://data.snf.ch/grants/grant/241892") == _URL


@pytest.mark.parametrize("bad", [None, "", "   ", "abc", "12a", [], {}, "https://example.com/1"])
def test_iri_rejects_garbage(bad):
    assert snsf_grant_iri(bad) is None


def test_parse_inverts():
    assert parse_snsf_grant(_URL) == 241892
    assert parse_snsf_grant("241892") == 241892
    assert parse_snsf_grant(241892) == 241892
    assert parse_snsf_grant(_URL + "/") == 241892


@pytest.mark.parametrize("bad", [None, "", "abc", "https://example.com/x"])
def test_parse_rejects_garbage(bad):
    assert parse_snsf_grant(bad) is None


def test_point_id_is_stable_uuid_keyed_on_url():
    # Same grant via int / str / URL → same point id (keyed on the URL).
    pid = snsf_grant_point_id(241892)
    assert pid == snsf_grant_point_id("241892")
    assert pid == snsf_grant_point_id(_URL)
    # uuid5 shape
    assert len(pid) == 36 and pid.count("-") == 4
    assert snsf_grant_point_id(999) != pid
