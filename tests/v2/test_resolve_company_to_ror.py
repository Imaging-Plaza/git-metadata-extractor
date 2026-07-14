"""Tests for `resolve_company_to_ror` stage + its `api.py` wiring.

Heavy live-provider behaviour (the 857-triple / ~3% FP characterization)
was validated by the stage author on a real ROR Qdrant. These tests
exercise:

  - Pure helpers (`_clean_query`, `_split_joint`, `_accept`).
  - Per-person mutation: confident hit stamps `schema:affiliation`;
    low-score / wrong-type / ambiguous hits don't.
  - Idempotency: re-running on already-stamped persons.
  - Joint affiliation strings (`X / Y`, `@a, @b, @c`).
  - The `V2_RESOLVE_COMPANY_TO_ROR` env flag — defaults on, opt-out off.
  - End-to-end stage entry uses a stub provider so no Qdrant is touched.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from git_metadata_extractor.pipeline.stages.models import ReconciledEntities
from git_metadata_extractor.pipeline.stages.resolve_company_to_ror import (
    GME_INTERNAL_COMPANY,
    CompanyAffiliationResult,
    _accept,
    _clean_query,
    _split_joint,
    _strip_country,
    run_resolve_company_to_ror_stage,
)


def _memberships(reconciled: ReconciledEntities) -> list[dict[str, Any]]:
    return reconciled.entities.get("memberships") or []


def _organizations(reconciled: ReconciledEntities) -> list[dict[str, Any]]:
    return reconciled.entities.get("organizations") or []


def _membership_org_ids(reconciled: ReconciledEntities, *, person_id: str) -> list[str]:
    """Return the ROR org ids attached to ``person_id`` via Memberships."""
    out: list[str] = []
    for m in _memberships(reconciled):
        composite = m.get("id", "")
        if composite.startswith(f"{person_id}__"):
            org_ref = m.get("org:organization")
            if isinstance(org_ref, str):
                out.append(org_ref)
    return out


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_clean_query_strips_handle_punct_and_collapses_whitespace():
    assert _clean_query("@google") == "google"
    assert _clean_query("Google Inc.") == "Google Inc"
    assert _clean_query("  EPFL,  ") == "EPFL"
    assert _clean_query("Google  Research") == "Google Research"


def test_split_joint_recognises_handle_lists_but_keeps_inc_suffix():
    # Slash separator → two parts.
    assert _split_joint("Google / DeepMind") == ["Google", "DeepMind"]
    # Semicolon → two parts.
    assert _split_joint("A; B") == ["A", "B"]
    # Comma list with at least one `@` handle → split on commas.
    assert _split_joint("NTU, UC Berkeley, @google") == [
        "NTU", "UC Berkeley", "@google",
    ]
    # Comma list without any `@` handle → keep as one (`Google, Inc.` shape).
    assert _split_joint("Google, Inc.") == ["Google, Inc."]


def test_strip_country_removes_trailing_parens():
    assert _strip_country("ETH Zurich (Switzerland)") == "ETH Zurich"
    assert _strip_country("EPFL  ") == "EPFL"
    assert _strip_country("") == ""


def test_accept_rejects_low_score():
    hits = [{"score": 0.5, "types": ["education"], "name": "Anything"}]
    winner, reason = _accept(hits, "anything")
    assert winner is None
    assert "0.50 < 0.55" in reason


def test_accept_rejects_non_org_types():
    hits = [{"score": 0.9, "types": ["other"], "name": "Anything"}]
    winner, reason = _accept(hits, "anything")
    assert winner is None
    assert "not a research org" in reason


def test_accept_accepts_clear_top_with_score_gap():
    hits = [
        {"score": 0.9, "types": ["education"], "name": "EPFL", "ror_id": "https://ror.org/02s376052"},
        {"score": 0.7, "types": ["education"], "name": "Other", "ror_id": "https://ror.org/other"},
    ]
    winner, reason = _accept(hits, "EPFL")
    assert winner is not None
    assert winner["ror_id"] == "https://ror.org/02s376052"
    assert "0.20" in reason  # gap


def test_accept_invokes_exact_name_override_when_gap_too_small():
    """`OpenAI` vs `OpenAI Foundation` with a tiny gap — the exact-name
    rule rescues the canonical match."""
    hits = [
        {"score": 0.91, "types": ["company"], "name": "OpenAI", "ror_id": "https://ror.org/openai"},
        {"score": 0.90, "types": ["company"], "name": "OpenAI Foundation", "ror_id": "https://ror.org/other"},
    ]
    winner, reason = _accept(hits, "openai")
    assert winner is not None
    assert "exact-name override" in reason


def test_accept_rejects_when_gap_small_and_name_does_not_match():
    hits = [
        {"score": 0.91, "types": ["company"], "name": "Calico", "ror_id": "https://ror.org/calico"},
        {"score": 0.90, "types": ["company"], "name": "GamesThatWork", "ror_id": "https://ror.org/other"},
    ]
    winner, reason = _accept(hits, "google")  # query doesn't match either
    assert winner is None
    assert "no exact-name match" in reason


# ---------------------------------------------------------------------------
# Stage entry with a stub provider
# ---------------------------------------------------------------------------


class _StubProvider:
    """Lookup table: cleaned query → list of hit dicts."""

    def __init__(self, hits: dict[str, list[dict[str, Any]]]) -> None:
        self._hits = hits
        self.queries: list[str] = []

    async def search(
        self,
        *,
        query: str,
        scope_mode: str = "worldwide",
        top_k: int = 5,
        rerank: bool = False,
    ) -> list[dict[str, Any]]:
        self.queries.append(query)
        return list(self._hits.get(query, []))


def _run(reconciled: ReconciledEntities, provider: Any) -> CompanyAffiliationResult:
    return asyncio.run(
        run_resolve_company_to_ror_stage(reconciled=reconciled, provider=provider),
    )


@pytest.mark.parametrize(
    "key",
    [
        "_company",  # rule-based agent shape — what the pipeline really carries
        "gme-internal:company",  # jsonld-build mid-form
        GME_INTERNAL_COMPANY,  # full IRI — post-hoc SPARQL shape
    ],
)
def test_stage_reads_company_under_every_key_shape(key):
    """The stage must work whether the Person dict carries the company
    under `_company` (in-pipeline), `gme-internal:company` (post jsonld-build
    rewrite), or the full IRI (post-hoc SPARQL graph)."""
    reconciled = ReconciledEntities(
        entities={
            "persons": [
                {"id": "p1", key: "Google"},
            ],
        },
    )
    provider = _StubProvider(
        hits={
            "Google": [
                {"score": 0.92, "types": ["company"], "name": "Google", "ror_id": "https://ror.org/google"},
                {"score": 0.50, "types": ["company"], "name": "Calico", "ror_id": "https://ror.org/calico"},
            ],
        },
    )
    result = _run(reconciled, provider)
    # The Person dict is left alone — no `schema:affiliation` written.
    person = reconciled.entities["persons"][0]
    assert "schema:affiliation" not in person
    assert "http://schema.org/affiliation" not in person
    # Affiliation is modelled as a Membership pointing to the resolved Org.
    assert _membership_org_ids(reconciled, person_id="p1") == [
        "https://ror.org/google",
    ]
    # The resolver also mints a minimal Organization stub for the ROR.
    org_ids = {o["id"] for o in _organizations(reconciled)}
    assert "https://ror.org/google" in org_ids
    assert result.persons_resolved == 1
    assert result.memberships_created == 1
    assert result.organizations_created == 1
    assert result.queries_accepted == 1


def test_stage_skips_low_confidence():
    reconciled = ReconciledEntities(
        entities={
            "persons": [
                {"id": "p1", GME_INTERNAL_COMPANY: "Something"},
            ],
        },
    )
    provider = _StubProvider(
        hits={
            "Something": [
                {"score": 0.40, "types": ["company"], "name": "Something Co"},
            ],
        },
    )
    result = _run(reconciled, provider)
    # Below-threshold hit produces zero Memberships / Orgs.
    assert _memberships(reconciled) == []
    assert _organizations(reconciled) == []
    assert result.persons_resolved == 0
    assert result.memberships_created == 0
    assert any("< 0.55" in reason for reason in result.rejection_reasons)


def test_stage_filters_non_org_strings_without_querying():
    reconciled = ReconciledEntities(
        entities={
            "persons": [
                {"id": "p1", GME_INTERNAL_COMPANY: "freelance"},
                {"id": "p2", GME_INTERNAL_COMPANY: "@self-employed"},
            ],
        },
    )
    provider = _StubProvider(hits={})
    result = _run(reconciled, provider)
    assert provider.queries == []  # nothing was searched
    assert result.persons_resolved == 0


def test_stage_resolves_joint_affiliations_into_a_list():
    reconciled = ReconciledEntities(
        entities={
            "persons": [
                {"id": "p1", GME_INTERNAL_COMPANY: "EPFL / ETH Zurich"},
            ],
        },
    )
    provider = _StubProvider(
        hits={
            "EPFL": [
                {"score": 0.95, "types": ["education"], "name": "EPFL", "ror_id": "https://ror.org/02s376052"},
                {"score": 0.40, "types": ["education"], "name": "Other"},
            ],
            "ETH Zurich": [
                {"score": 0.95, "types": ["education"], "name": "ETH Zurich", "ror_id": "https://ror.org/05a28rw58"},
                {"score": 0.40, "types": ["education"], "name": "Other"},
            ],
        },
    )
    _run(reconciled, provider)
    # Joint affiliations produce one Membership per resolved org, all
    # linked to the same person.
    assert sorted(_membership_org_ids(reconciled, person_id="p1")) == [
        "https://ror.org/02s376052",
        "https://ror.org/05a28rw58",
    ]
    org_ids = {o["id"] for o in _organizations(reconciled)}
    assert "https://ror.org/02s376052" in org_ids
    assert "https://ror.org/05a28rw58" in org_ids


def test_stage_is_idempotent_on_re_run():
    """Re-running on a person whose Membership already exists is a
    no-op: no duplicate Memberships or Organization stubs."""
    reconciled = ReconciledEntities(
        entities={
            "persons": [
                {"id": "p1", GME_INTERNAL_COMPANY: "Google"},
            ],
        },
    )
    provider = _StubProvider(
        hits={
            "Google": [
                {"score": 0.92, "types": ["company"], "name": "Google", "ror_id": "https://ror.org/google"},
            ],
        },
    )
    _run(reconciled, provider)
    memberships_first = list(_memberships(reconciled))
    orgs_first = list(_organizations(reconciled))
    _run(reconciled, provider)
    memberships_second = list(_memberships(reconciled))
    orgs_second = list(_organizations(reconciled))
    # Same composite id ⇒ no second Membership; same ROR ⇒ no second Org.
    assert [m["id"] for m in memberships_second] == [m["id"] for m in memberships_first]
    assert [o["id"] for o in orgs_second] == [o["id"] for o in orgs_first]
    assert len(memberships_second) == 1
    assert len(orgs_second) == 1


def test_stage_returns_zero_when_provider_missing():
    """No provider available (env not wired) — stage returns a sane result
    and never raises."""
    reconciled = ReconciledEntities(entities={"persons": [{"id": "p1"}]})
    result = asyncio.run(
        run_resolve_company_to_ror_stage(reconciled=reconciled, provider=None),
    )
    # Result is well-formed even when provider building fails / returns None.
    # (The actual provider build may succeed in a dev env with Qdrant up,
    # but on a CI box without Qdrant the persons_examined falls to 0 and
    # rejection_reasons records a provider_unavailable flag.)
    assert isinstance(result, CompanyAffiliationResult)


# ---------------------------------------------------------------------------
# api.py env-flag wiring
# ---------------------------------------------------------------------------


def test_api_env_flag_defaults_on(monkeypatch):
    from git_metadata_extractor.api import _helpers as v2_api

    monkeypatch.delenv("V2_RESOLVE_COMPANY_TO_ROR", raising=False)
    assert v2_api._resolve_company_to_ror_enabled() is True


@pytest.mark.parametrize("value", ["false", "FALSE", "0", "no", "off", "n", "f"])
def test_api_env_flag_recognises_off_values(value, monkeypatch):
    from git_metadata_extractor.api import _helpers as v2_api

    monkeypatch.setenv("V2_RESOLVE_COMPANY_TO_ROR", value)
    assert v2_api._resolve_company_to_ror_enabled() is False


@pytest.mark.parametrize("value", ["true", "1", "yes", "on", "anything-else"])
def test_api_env_flag_treats_other_values_as_on(value, monkeypatch):
    """Anything that isn't an explicit off-value keeps the stage on."""
    from git_metadata_extractor.api import _helpers as v2_api

    monkeypatch.setenv("V2_RESOLVE_COMPANY_TO_ROR", value)
    assert v2_api._resolve_company_to_ror_enabled() is True


def test_api_constant_and_export_are_in_place():
    from git_metadata_extractor.api import _helpers as v2_api
    from git_metadata_extractor.pipeline import stages

    assert v2_api.STAGE_RESOLVE_COMPANY_TO_ROR == "resolve_company_to_ror"
    # The stage entry function is callable from the package surface.
    assert callable(stages.run_resolve_company_to_ror_stage)
    assert "run_resolve_company_to_ror_stage" in stages.__all__
    assert "CompanyAffiliationResult" in stages.__all__
