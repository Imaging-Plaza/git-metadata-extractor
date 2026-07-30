# tests/v2/test_summarize_badges.py
"""Bug 08: badges collapsed to content-free blank nodes on JSON-LD expansion
because the nested {label,image_url,link_url} keys are unmapped in @context.
summarize_badges() derives flat, index-aligned scalar lists that emit as
literal gme-internal: triples a consumer can actually read.
"""
from __future__ import annotations

from git_metadata_extractor.agents.rule_based._repo_signals import summarize_badges

_EMPTY = {"badge_labels": None, "badge_image_urls": None, "badge_links": None}


def test_flat_aligned_lists():
    badges = [
        {"label": "build: passing", "image_url": "https://img.shields.io/b.svg",
         "link_url": "https://ci/run"},
        {"label": "PyPI", "image_url": "https://img.shields.io/pypi.svg",
         "link_url": None},  # no link → "" placeholder keeps alignment
    ]
    out = summarize_badges(badges)
    assert out["badge_labels"] == ["build: passing", "PyPI"]
    assert out["badge_image_urls"] == [
        "https://img.shields.io/b.svg", "https://img.shields.io/pypi.svg",
    ]
    assert out["badge_links"] == ["https://ci/run", ""]
    # all three lists stay index-aligned
    assert len({len(out["badge_labels"]), len(out["badge_image_urls"]),
                len(out["badge_links"])}) == 1


def test_empty_or_missing_returns_none_keys():
    assert summarize_badges(None) == _EMPTY
    assert summarize_badges([]) == _EMPTY
    assert summarize_badges("not-a-list") == _EMPTY


def test_empty_alt_text_becomes_empty_string_not_dropped():
    out = summarize_badges([{"label": "", "image_url": "https://x.svg", "link_url": None}])
    assert out["badge_labels"] == [""]
    assert out["badge_image_urls"] == ["https://x.svg"]
    assert out["badge_links"] == [""]
