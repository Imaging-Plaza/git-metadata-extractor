"""Unit tests for CFF date normalization in ``gimie_methods``."""
# ruff: noqa: SLF001

from __future__ import annotations

import yaml

from src.gimie_utils import gimie_methods as gm


def test_normalize_cff_swaps_day_month_when_middle_gt_12():
    raw = b"cff-version: 1.2.0\ndate-released: 2025-28-04\n"
    out = gm._normalize_cff_calendar_dates(raw).decode()
    assert "date-released: 2025-04-28" in out


def test_normalize_cff_leaves_valid_iso_unchanged():
    raw = b"date-released: 2025-04-28\n"
    out = gm._normalize_cff_calendar_dates(raw).decode()
    assert out == raw.decode()


def test_normalize_cff_quotes_nonsense_template_dates():
    """Template values like 2025-13-14 are not fixable by swap; must be quoted."""
    raw = b"date-released: 2025-13-14\n"
    out = gm._normalize_cff_calendar_dates(raw).decode()
    assert 'date-released: "2025-13-14"' in out
    loaded = yaml.safe_load(out)
    assert loaded["date-released"] == "2025-13-14"


def test_rewrite_cff_known_date_value_branches():
    assert gm._rewrite_cff_known_date_value("2025-28-04") == (
        "2025-04-28",
        "normalized swapped day/month",
    )
    assert gm._rewrite_cff_known_date_value("2025-04-28") is None
    assert gm._rewrite_cff_known_date_value("2025-53-05") == (
        '"2025-53-05"',
        "quoted invalid calendar (YAML timestamp escape)",
    )
    assert gm._rewrite_cff_known_date_value('"2025-13-14"') is None
