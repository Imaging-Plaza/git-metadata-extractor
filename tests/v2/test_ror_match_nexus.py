"""The ROR-parent nexus guard rejects coincidental matches (field report #7).

The owner→ROR resolver seeds its candidate shortlist with generic
single-token ROR queries (``foundry``, ``ai``, ``planet``), so the LLM
selector can confidently pick a company that shares *nothing* with the org
(``Edinburgh-Genome-Foundry`` → "Jøtul", ``unionai`` → "Ai Corporation").
``_ror_match_has_nexus`` is the post-selection guard: it keeps a pick only
when there is a real signal — a token/alias/acronym overlap OR a ROR-name
token (≥4 chars) that is a substring of the de-separated handle/name (which
recovers concatenated handles like ``broadinstitute`` → "Broad Institute").
"""

from __future__ import annotations

import pytest

from src.v2.pipeline.stages.ownership_check import _ror_match_has_nexus


def _rec(name: str, *, aliases: list[str] | None = None, acronyms: list[str] | None = None) -> dict:
    return {"name": name, "aliases": aliases or [], "acronyms": acronyms or []}


# Coincidental matches from the field report — must be rejected (no nexus).
@pytest.mark.parametrize(
    ("handle", "ror_name"),
    [
        ("Edinburgh-Genome-Foundry", "Jøtul (Norway)"),
        ("unionai", "Ai Corporation (United Kingdom)"),
        ("C-CoMP-STC", "Planet"),
        ("AI-Ecology-Lab", "NAVER Cloud (South Korea)"),
        ("AndersenLab", "Accenture (Italy)"),
        ("AG-Walz", "Stealth BioTherapeutics (United States)"),
        ("broadinstitute", "Microsoft"),  # right shape, wrong org
    ],
)
def test_coincidental_match_rejected(handle: str, ror_name: str) -> None:
    assert _ror_match_has_nexus(handle=handle, name=None, ror_record=_rec(ror_name)) is False


# Correct matches that tokenize to a single token or an acronym — must survive.
@pytest.mark.parametrize(
    ("handle", "ror_name", "acronyms"),
    [
        ("broadinstitute", "Broad Institute", []),
        ("huggingface", "Hugging Face", []),
        ("FrancisCrickInstitute", "The Francis Crick Institute", []),
        ("opentargets", "Open Targets", []),
        ("nanoporetech", "Oxford Nanopore Technologies (United Kingdom)", []),
        ("ssi-dk", "Statens Serum Institut", ["SSI"]),
        ("google-deepmind", "Google DeepMind (United Kingdom)", []),
    ],
)
def test_real_match_kept(handle: str, ror_name: str, acronyms: list[str]) -> None:
    rec = _rec(ror_name, acronyms=acronyms)
    assert _ror_match_has_nexus(handle=handle, name=None, ror_record=rec) is True


def test_short_token_does_not_match_coincidentally() -> None:
    # "ai" (<4 chars) inside "unionai" must NOT count as a substring nexus.
    assert _ror_match_has_nexus(
        handle="unionai", name=None, ror_record=_rec("Ai Corporation"),
    ) is False
