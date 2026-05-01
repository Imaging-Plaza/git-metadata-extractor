"""Tests for ORCID-specific text builders + chunking."""

from __future__ import annotations

from src.index.orcid.embed.chunker import (
    chunk_for_affiliation,
    chunk_for_person,
    person_card_text,
)


def test_person_card_text_includes_name_bio_affiliations() -> None:
    row = {
        "display_name": "Alice Example",
        "biography": "Researches quantum graph compilers.",
    }
    text = person_card_text(row, ["EPFL", "ETHZ"])
    assert text is not None
    assert "Alice Example" in text
    assert "quantum graph compilers" in text
    assert "EPFL" in text and "ETHZ" in text


def test_person_card_text_returns_none_when_empty() -> None:
    assert person_card_text({}, []) is None


def test_chunk_for_person_yields_at_least_one_chunk() -> None:
    row = {"display_name": "Alice Example", "biography": "Quantum compilers."}
    chunks = chunk_for_person(row, ["EPFL"], chunk_tokens=64, overlap=8)
    assert len(chunks) >= 1
    assert chunks[0].text.startswith("Alice Example")


def test_chunk_for_affiliation_uses_role_and_dates() -> None:
    row = {
        "organization": "EPFL",
        "department": "School of Engineering",
        "role": "Research Engineer",
        "start_date": "2021-01-01",
        "end_date": None,
    }
    chunks = chunk_for_affiliation(
        "Alice Example",
        row,
        chunk_tokens=128,
        overlap=8,
    )
    assert len(chunks) == 1
    body = chunks[0].text
    assert "Alice Example" in body
    assert "Research Engineer" in body
    assert "EPFL" in body
    assert "School of Engineering" in body
    assert "2021-01-01" in body
    assert "present" in body
