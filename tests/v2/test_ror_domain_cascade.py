"""Web-domain evidence (Tier A1) in the owner→ROR resolver (field report §7).

The decisive, zero-cost signal: compare the org's registrable web domain with
the ROR record's. A match accepts (broadinstitute.org == www.broadinstitute.org);
a mismatch rejects (cloud.google.com vs deepmind.google; opig.stats.ox.ac.uk vs
oxfordresearchgroup.org.uk). This catches the token-coincidence residual the
name-only nexus guard can't. Requires the ROR v2 `links` (objects) / `domains`
fix in the provider — without it the GME never saw a ROR domain.
"""

from __future__ import annotations

import asyncio

import pytest

from git_metadata_extractor.providers.ror_provider import _normalize_ror_organization
from git_metadata_extractor.pipeline.stages.ownership_check import (
    _org_domain_label,
    _registrable_label,
    _ror_domain_labels,
    _select_ror_parent,
)


# --- registrable-label extraction -------------------------------------------
@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("cloud.google.com", "google"),
        ("deepmind.google", "deepmind"),
        ("www.broadinstitute.org", "broadinstitute"),
        ("opig.stats.ox.ac.uk", "ox"),  # 2-part suffix ac.uk
        ("oxfordresearchgroup.org.uk", "oxfordresearchgroup"),  # org.uk
        ("nanoporetech.com", "nanoporetech"),
    ],
)
def test_registrable_label(host: str, expected: str) -> None:
    assert _registrable_label(host) == expected


def test_org_domain_label_ignores_generic_hosts() -> None:
    assert _org_domain_label({"homepage": "https://edinburgh-genome-foundry.github.io"}) is None
    assert _org_domain_label({"homepage": "https://www.broadinstitute.org"}) == "broadinstitute"
    assert _org_domain_label(None) is None


# --- ROR v2 links/domains parsing (provider) --------------------------------
def test_normalize_ror_v2_links_objects_and_domains() -> None:
    item = {
        "id": "https://ror.org/00971b260",
        "names": [{"types": ["ror_display"], "value": "Google DeepMind (United Kingdom)"}],
        "links": [
            {"type": "website", "value": "https://deepmind.google"},
            {"type": "wikipedia", "value": "https://en.wikipedia.org/wiki/DeepMind"},
        ],
        "domains": ["deepmind.google"],
    }
    rec = _normalize_ror_organization(item)
    assert "https://deepmind.google" in rec["links"]
    assert _ror_domain_labels(rec) == {"deepmind"}  # wikipedia dropped as generic


# --- cascade ----------------------------------------------------------------
def _hit(ror_id: str, name: str, *, links: list[str] | None = None) -> dict:
    return {"id": ror_id, "name": name, "aliases": [], "acronyms": [], "links": links or []}


def _select(shortlist, *, org_name, homepage):
    return asyncio.run(
        _select_ror_parent(
            handle=org_name.replace(" ", "-"),
            org_name=org_name,
            shortlist=shortlist,
            parent_selector=None,  # rule-based; A1 + guards still apply
            org_context={"homepage": homepage} if homepage else None,
            warnings=[],
        ),
    )


def test_a1_domain_match_is_decisive() -> None:
    # Low token score, but the web domain matches -> accepted decisively.
    shortlist = [(1, _hit("ror:broad", "Broad Institute", links=["https://www.broadinstitute.org"]))]
    pick = _select(shortlist, org_name="broadinstitute", homepage="https://www.broadinstitute.org")
    assert pick is not None and pick["id"] == "ror:broad"


def test_a1_domain_mismatch_rejects_a_token_winner() -> None:
    # GCP-style: the token winner's domain differs from the org's -> reject.
    shortlist = [(3, _hit("ror:dm", "Google DeepMind", links=["https://deepmind.google"]))]
    pick = _select(shortlist, org_name="Google Cloud Platform", homepage="https://cloud.google.com")
    assert pick is None


def test_no_domain_falls_back_to_nexus_guard() -> None:
    # Org has no usable domain (github.io); a generic-token coincidence with no
    # distinctive nexus is rejected by Tier B.
    shortlist = [(2, _hit("ror:jotul", "Jøtul (Norway)", links=["http://jotul.com"]))]
    pick = _select(
        shortlist,
        org_name="Edinburgh Genome Foundry",
        homepage="https://edinburgh-genome-foundry.github.io",
    )
    assert pick is None


# --- A2 external-id / A3 exact-name / provenance (cascade tiers) -------------
def _hit_ext(ror_id: str, name: str, *, links=None, external_ids=None, aliases=None, acronyms=None):
    return {
        "id": ror_id, "name": name, "aliases": aliases or [], "acronyms": acronyms or [],
        "links": links or [], "external_ids": external_ids or {},
    }


def test_a3_exact_name_match_accepts_with_provenance() -> None:
    # No domain; exact normalised name equality -> A3 accept, tier stamped.
    shortlist = [(1, _hit_ext("ror:hf", "Hugging Face"))]
    pick = asyncio.run(
        _select_ror_parent(
            handle="huggingface", org_name="Hugging Face", shortlist=shortlist,
            parent_selector=None, org_context=None, warnings=[],
        ),
    )
    assert pick is not None and pick["id"] == "ror:hf"
    assert pick["_ror_match_tier"] == "A3_exact_name"
    assert pick["_ror_match_confidence"] == 0.95


def test_a2_external_id_match_accepts() -> None:
    shortlist = [(0, _hit_ext("ror:x", "Some Org", external_ids={"grid": "grid.42"}))]
    pick = asyncio.run(
        _select_ror_parent(
            handle="someorg", org_name="Some Org", shortlist=shortlist,
            parent_selector=None,
            org_context={"external_ids": ["grid:grid.42"]}, warnings=[],
        ),
    )
    assert pick is not None and pick["_ror_match_tier"] == "A2_external_id"


def test_a1_domain_wins_over_a3_exact_name() -> None:
    # One candidate matches by exact name, another (the real one) by domain.
    shortlist = [
        (5, _hit_ext("ror:wrong", "Acme", links=["https://example.org"])),  # exact name only
        (1, _hit_ext("ror:right", "Acme Labs", links=["https://acme.com"])),  # domain match
    ]
    pick = asyncio.run(
        _select_ror_parent(
            handle="acme", org_name="Acme", shortlist=shortlist,
            parent_selector=None, org_context={"homepage": "https://www.acme.com"}, warnings=[],
        ),
    )
    assert pick is not None and pick["id"] == "ror:right"
    assert pick["_ror_match_tier"] == "A1_domain"


# --- A1 parent-domain-collision disambiguation ------------------------------
def _hd(ror_id: str, name: str, domain: str):
    return _hit_ext(ror_id, name, links=[f"https://{domain}"])


def test_a1_regional_variants_prefers_global_record() -> None:
    shortlist = [
        (1, _hd("g:ca", "Google (Canada)", "google.com")),
        (1, _hd("g:global", "Google", "google.com")),
        (1, _hd("g:us", "Google (United States)", "google.com")),
    ]
    pick = asyncio.run(
        _select_ror_parent(
            handle="GoogleCloudPlatform", org_name="Google Cloud Platform",
            shortlist=shortlist, parent_selector=None,
            org_context={"homepage": "https://cloud.google.com"}, warnings=[],
        ),
    )
    assert pick is not None and pick["id"] == "g:global"
    assert pick["_ror_match_tier"] == "A1_domain"


def test_a1_all_regional_no_global_abstains() -> None:
    # No unqualified 'Google' record -> A1 abstains; rule-based token<2 -> None.
    shortlist = [
        (1, _hd("g:ca", "Google (Canada)", "google.com")),
        (1, _hd("g:us", "Google (United States)", "google.com")),
    ]
    pick = asyncio.run(
        _select_ror_parent(
            handle="GoogleCloudPlatform", org_name="Google Cloud Platform",
            shortlist=shortlist, parent_selector=None,
            org_context={"homepage": "https://cloud.google.com"}, warnings=[],
        ),
    )
    assert pick is None  # no arbitrary region picked


def test_a1_umbrella_domain_defers_to_name_match() -> None:
    # NLM and NHLBI both nih.gov (distinct sub-entities) -> A1 abstains; the
    # exact-name / token tier then picks the RIGHT one (NLM, not NHLBI).
    shortlist = [
        (2, _hd("nhlbi", "National Heart, Lung, and Blood Institute", "nih.gov")),
        (2, _hd("nlm", "National Library of Medicine", "nih.gov")),
    ]
    pick = asyncio.run(
        _select_ror_parent(
            handle="NLM-DIR", org_name="National Library of Medicine",
            shortlist=shortlist, parent_selector=None,
            org_context={"homepage": "https://www.nlm.nih.gov"}, warnings=[],
        ),
    )
    assert pick is not None and pick["id"] == "nlm"
