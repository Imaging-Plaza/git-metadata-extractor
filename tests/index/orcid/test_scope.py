"""Post-filter tests for the ORCID scope module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.index.orcid.ingest.scope import post_filter_record

FIXTURE_DIR = Path("tests/v2/fixtures/providers/orcid")


def _load(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_epfl_scope_matches_alias(base_config) -> None:
    record = _load("valid_record.json")
    in_scope, reason = post_filter_record(
        record,
        scope="epfl",
        config=base_config,
        discovered_via="orcid_search",
    )
    assert in_scope is True
    assert reason is not None
    assert "polytechnique" in reason.lower() or "epfl" in reason.lower()


def test_epfl_scope_drops_unrelated(base_config) -> None:
    record = _load("no_affiliations.json")
    in_scope, reason = post_filter_record(
        record,
        scope="epfl",
        config=base_config,
        discovered_via="orcid_search",
    )
    assert in_scope is False
    assert reason is None


def test_switzerland_trusts_openalex_seed(base_config) -> None:
    record = _load("no_affiliations.json")
    in_scope, reason = post_filter_record(
        record,
        scope="switzerland",
        config=base_config,
        discovered_via="openalex",
    )
    assert in_scope is True
    assert reason is not None
    assert "openalex" in reason.lower()


def test_unknown_scope_raises(base_config) -> None:
    record = _load("valid_record.json")
    with pytest.raises(ValueError, match="Unknown scope"):
        post_filter_record(
            record,
            scope="mars",  # type: ignore[arg-type]
            config=base_config,
            discovered_via="manual",
        )
