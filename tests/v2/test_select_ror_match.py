"""Tests for `_select_ror_match` — the rule-based org agent's ROR picker.

The picker used to accept a match on a single shared token, so it bound
`Center for Digital Trust` (C4DT) to "RISM **Digital Center**" and
`Imaging-Plaza` to "Kanazawa Education **Plaza**". It now requires a
*distinctive* (non-generic) shared token; weak matches return None, which
leaves `pulse:ror` null so the agent-backed parent selector decides.
"""

from __future__ import annotations

import pytest

from git_metadata_extractor.agents.rule_based.organization_agent import (
    _select_ror_match,
)


def _ror(name: str, *, ror_id: str, country: str = "CH") -> dict:
    return {
        "id": ror_id,
        "name": name,
        "aliases": [],
        "acronyms": [],
        "labels": [],
        "country": {"country_code": country},
    }


def test_generic_only_overlap_is_rejected() -> None:
    """`Center for Digital Trust` vs `RISM Digital Center` share only the
    generic words 'center' and 'digital' — a coincidental collision."""
    warnings: list[str] = []
    result = _select_ror_match(
        [_ror("RISM Digital Center", ror_id="https://ror.org/01kk1vy78")],
        country_bias=None,
        warnings=warnings,
        ror_query="Center for Digital Trust",
    )
    assert result is None
    assert any("phantom" in w for w in warnings)


@pytest.mark.parametrize(
    ("ror_query", "ror_name"),
    [
        # Production: `Imaging-Plaza` vs `Kanazawa Education Plaza`.
        ("Imaging-Plaza", "Kanazawa Education Plaza"),
        # Each remaining row shares exactly one generic word, so it pins
        # that word's membership in the generic set by behaviour.
        ("Imaging Centre", "Kanazawa Centre"),
        ("Imaging Lab", "Kanazawa Lab"),
        ("Imaging Laboratory", "Kanazawa Laboratory"),
    ],
)
def test_single_generic_token_collision_is_rejected(ror_query: str, ror_name: str) -> None:
    """A match sharing only one generic word with the query is declined."""
    result = _select_ror_match(
        [_ror(ror_name, ror_id="https://ror.org/0412v5t33")],
        country_bias=None,
        warnings=[],
        ror_query=ror_query,
    )
    assert result is None


def test_distinctive_overlap_is_accepted() -> None:
    """A genuine match shares distinctive tokens ('swiss', 'data', 'science')."""
    sdsc = _ror("Swiss Data Science Center", ror_id="https://ror.org/02hdt9m26")
    result = _select_ror_match(
        [sdsc],
        country_bias=None,
        warnings=[],
        ror_query="Swiss Data Science Center",
    )
    assert result is sdsc


def test_country_bias_rescue_still_requires_distinctive_token() -> None:
    """The country-bias path must not 'rescue' a generic-only CH collision —
    this is exactly the C4DT → RISM production bug."""
    non_ch = _ror(
        "Digital Trust Center", ror_id="https://ror.org/04jthgw29", country="US",
    )
    ch_rism = _ror("RISM Digital Center", ror_id="https://ror.org/01kk1vy78")
    result = _select_ror_match(
        [non_ch, ch_rism],
        country_bias="CH",
        warnings=[],
        ror_query="Center for Digital Trust",
    )
    assert result is None


def test_country_bias_accepts_distinctive_ch_match() -> None:
    us_top = _ror(
        "San Diego Supercomputer Center",
        ror_id="https://ror.org/05cvfcr44",
        country="US",
    )
    ch_sdsc = _ror("Swiss Data Science Center", ror_id="https://ror.org/02hdt9m26")
    result = _select_ror_match(
        [us_top, ch_sdsc],
        country_bias="CH",
        warnings=[],
        ror_query="Swiss Data Science Center",
    )
    assert result is ch_sdsc
