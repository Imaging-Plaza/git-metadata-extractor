"""The ROR-parent nexus guard rejects coincidental matches (field report #7).

ROR's free-text ranking returns a token-coincident org at #1 even on an idle
deploy with the full result list — e.g. "Edinburgh Genome Foundry" → "Jøtul
(Norway)" (ror 042epp307), because Jøtul's aliases include "Kværner **Foundry**".
The github→ROR resolver accepted ROR's #1 without verifying the name.

``_ror_match_has_nexus`` keeps a match only when there is a *distinctive*
(non-generic) shared token, an acronym match, or a distinctive substring — so a
generic-word-only overlap ("foundry", "ai", "genomics") is rejected while
concatenated handles ("broadinstitute" → "Broad Institute") and acronyms
("ssi-dk" → SSI) survive. Records here mirror the LIVE ROR shapes (incl.
aliases), which is what exposed the earlier, weaker guard.
"""

from __future__ import annotations

import pytest

from src.v2.pipeline.stages.ownership_check import _ror_match_has_nexus


def _rec(name: str, *, aliases: list[str] | None = None, acronyms: list[str] | None = None) -> dict:
    return {"name": name, "aliases": aliases or [], "acronyms": acronyms or []}


# Coincidental matches — generic-word-only overlap — must be rejected. Records
# carry the real aliases that made the naive guard fail.
@pytest.mark.parametrize(
    ("handle", "name", "record"),
    [
        # Jøtul's "Kværner Foundry" alias shares only the generic "foundry".
        (
            "Edinburgh-Genome-Foundry",
            "Edinburgh Genome Foundry",
            _rec("Jøtul (Norway)", aliases=["Kværner Foundry", "Kværner Jernstøberi"]),
        ),
        ("unionai", "unionai", _rec("Ai Corporation (United Kingdom)")),  # only "ai"/"corporation"
        ("C-CoMP-STC", "C-CoMP-STC", _rec("Planet")),
        ("AI-Ecology-Lab", "AI Ecology Lab", _rec("NAVER Cloud (South Korea)")),  # "lab"/"cloud"
        ("AndersenLab", "AndersenLab", _rec("Accenture (Italy)")),
        ("broadinstitute", "Broad Institute", _rec("Microsoft")),  # right shape, wrong org
    ],
)
def test_coincidental_match_rejected(handle: str, name: str, record: dict) -> None:
    assert _ror_match_has_nexus(handle=handle, name=name, ror_record=record) is False


# Correct matches (concatenated handles / acronyms / distinctive tokens) survive.
@pytest.mark.parametrize(
    ("handle", "name", "record"),
    [
        ("broadinstitute", "Broad Institute", _rec("Broad Institute")),
        ("huggingface", "Hugging Face", _rec("Hugging Face")),
        ("FrancisCrickInstitute", "Francis Crick Institute", _rec("The Francis Crick Institute")),
        ("opentargets", "Open Targets", _rec("Open Targets")),
        ("nanoporetech", "Oxford Nanopore", _rec("Oxford Nanopore Technologies (United Kingdom)")),
        ("ssi-dk", "ssi-dk", _rec("Statens Serum Institut", acronyms=["SSI"])),
        ("google-deepmind", "google-deepmind", _rec("Google DeepMind (United Kingdom)")),
    ],
)
def test_real_match_kept(handle: str, name: str, record: dict) -> None:
    assert _ror_match_has_nexus(handle=handle, name=name, ror_record=record) is True


def test_generic_alias_token_is_not_a_nexus() -> None:
    # The exact #102 regression: a generic token shared only via a ROR alias
    # ("foundry" in "Kværner Foundry") must NOT count.
    rec = _rec("Jøtul (Norway)", aliases=["Kværner Foundry"])
    assert _ror_match_has_nexus(handle="Edinburgh-Genome-Foundry", name="Edinburgh Genome Foundry", ror_record=rec) is False
