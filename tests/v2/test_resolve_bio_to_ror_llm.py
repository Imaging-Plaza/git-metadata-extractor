"""Tests for the LLM bio-resolver stage (`resolve_bio_to_ror_llm`).

The stage runs Stage B of the affiliation-from-richer-Person-signals
track. We exercise:

  - Candidate selection: only persons missing `schema:affiliation` AND
    with at least one of bio / orcid bio / readme are sent to the LLM.
  - Patch application: ROR with confidence ≥ 0.7 → stamp; below floor →
    no-op; per-person LLM failure → warning, never raises.
  - Concurrency env var parsing.
  - Wiring: env flag, api stage constant.

A `_StubAgent` returns canned patches keyed by person id so we never
touch a real LLM runtime or Qdrant.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from git_metadata_extractor.agents.llm.refiners.bio_resolver import (
    BioResolverInput,
    BioResolverPatch,
)
from git_metadata_extractor.agents.llm.runtime import LLMRuntimeError
from git_metadata_extractor.pipeline.stages.models import ReconciledEntities
from git_metadata_extractor.pipeline.stages.resolve_bio_to_ror_llm import (
    BioLLMAffiliationResult,
    _apply_patch,
    _resolve_max_concurrency,
    run_resolve_bio_to_ror_llm_stage,
)
def _memberships(reconciled: ReconciledEntities) -> list[dict[str, Any]]:
    return reconciled.entities.get("memberships") or []


def _membership_org_ids(reconciled: ReconciledEntities, *, person_id: str) -> list[str]:
    return [
        m["org:organization"]
        for m in _memberships(reconciled)
        if isinstance(m, dict)
        and isinstance(m.get("id"), str)
        and m["id"].startswith(f"{person_id}__")
    ]


def _seed_membership(
    reconciled: ReconciledEntities, *, person_id: str, ror: str,
) -> None:
    composite = f"{person_id}__{ror}"
    reconciled.entities.setdefault("memberships", []).append(
        {
            "id": composite,
            "type": "org:Membership",
            "shacl": "pulse:MembershipShape",
            "identifiers": {"pulse:composite": composite, "uuid": "u"},
            "idSource": "pulse:composite",
            "org:organization": ror,
            "org:role": None,
            "time:hasBeginning": None,
            "time:hasEnd": None,
        },
    )


class _StubAgent:
    """Lookup table: person id → canned patch (or an exception class to raise)."""

    def __init__(self, patches: dict[str, Any]) -> None:
        self._patches = patches
        self.calls: list[str] = []

    async def run(
        self,
        *,
        refiner_input: BioResolverInput,
        tools: list[object] | None = None,
    ) -> BioResolverPatch:
        pid = refiner_input.person.person_id
        self.calls.append(pid)
        spec = self._patches.get(pid)
        if isinstance(spec, BaseException):
            raise spec
        if spec is None:
            return BioResolverPatch()
        return spec


class _StubProvider:
    """Minimal ROR provider stub — only needs to satisfy the
    `make_ror_rag_search_tool` factory."""

    async def search(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return []


def _run(reconciled: ReconciledEntities, *, agent: Any, provider: Any = None) -> BioLLMAffiliationResult:
    return asyncio.run(
        run_resolve_bio_to_ror_llm_stage(
            reconciled=reconciled,
            provider=provider or _StubProvider(),
            agent=agent,
            max_concurrency=2,
        ),
    )


# ---------------------------------------------------------------------------
# Candidate selection
# ---------------------------------------------------------------------------


def test_only_unaffiliated_persons_with_text_are_sent_to_the_llm():
    """Skip persons that already have a Membership, and skip persons
    with no bio / orcid / readme — the LLM has nothing to quote."""
    reconciled = ReconciledEntities(
        entities={
            "persons": [
                {"id": "already", "_bio": "Engineer at X"},
                {"id": "empty"},  # no signal at all
                {"id": "with-bio", "_bio": "Research Engineer at DeepMind"},
                {"id": "with-readme", "_profile_readme": "# About me\nI work at MIT."},
            ],
        },
    )
    _seed_membership(reconciled, person_id="already", ror="https://ror.org/x")
    agent = _StubAgent(patches={})  # returns no-op for every call
    result = _run(reconciled, agent=agent)
    # Only the last two were sent.
    assert sorted(agent.calls) == ["with-bio", "with-readme"]
    assert result.persons_examined == 4
    assert result.persons_called == 2
    assert result.persons_resolved == 0


def test_no_op_when_every_person_already_affiliated():
    reconciled = ReconciledEntities(
        entities={
            "persons": [{"id": "p1", "_bio": "..."}],
        },
    )
    _seed_membership(reconciled, person_id="p1", ror="https://ror.org/x")
    agent = _StubAgent(patches={})
    result = _run(reconciled, agent=agent)
    assert result.persons_called == 0
    assert agent.calls == []


# ---------------------------------------------------------------------------
# Patch application
# ---------------------------------------------------------------------------


def test_high_confidence_ror_creates_membership_and_organization():
    reconciled = ReconciledEntities(
        entities={
            "persons": [
                {"id": "p1", "_bio": "Senior Research Engineer at Google DeepMind"},
            ],
        },
    )
    agent = _StubAgent(
        patches={
            "p1": BioResolverPatch(
                pulse_ror="https://ror.org/deepmind",
                reason="Senior Research Engineer at Google DeepMind",
                confidence=0.92,
            ),
        },
    )
    result = _run(reconciled, agent=agent)
    assert _membership_org_ids(reconciled, person_id="p1") == [
        "https://ror.org/deepmind",
    ]
    org_ids = {o["id"] for o in reconciled.entities.get("organizations", [])}
    assert "https://ror.org/deepmind" in org_ids
    assert result.persons_resolved == 1
    assert result.memberships_created == 1
    assert result.organizations_created == 1
    assert result.persons_failed == 0


def _apply_kwargs(
    *, reconciled: ReconciledEntities, existing_org_ids: set, existing_keys: set,
) -> dict[str, Any]:
    return {
        "reconciled": reconciled,
        "existing_org_ids": existing_org_ids,
        "existing_membership_keys": existing_keys,
    }


def test_low_confidence_ror_is_dropped_at_apply_time():
    """The stage-side `_apply_patch` re-checks the confidence floor
    defensively — a low-confidence patch produces zero Memberships."""
    reconciled = ReconciledEntities(entities={"persons": [{"id": "p1"}]})
    person = reconciled.entities["persons"][0]
    patch = BioResolverPatch(
        pulse_ror="https://ror.org/x",
        reason="vague",
        confidence=0.5,
    )
    m_added, o_added = _apply_patch(
        person=person, patch=patch,
        **_apply_kwargs(reconciled=reconciled, existing_org_ids=set(), existing_keys=set()),
    )
    assert (m_added, o_added) == (0, 0)
    assert _memberships(reconciled) == []


def test_apply_patch_adds_a_second_membership_when_person_has_one_already():
    """An existing Membership to ROR /existing doesn't prevent a new
    Membership to ROR /new from being created — they're distinct
    composites."""
    reconciled = ReconciledEntities(entities={"persons": [{"id": "p1"}]})
    _seed_membership(reconciled, person_id="p1", ror="https://ror.org/existing")
    patch = BioResolverPatch(
        pulse_ror="https://ror.org/new",
        reason="From bio: ...",
        confidence=0.9,
    )
    m_added, o_added = _apply_patch(
        person=reconciled.entities["persons"][0],
        patch=patch,
        **_apply_kwargs(
            reconciled=reconciled,
            existing_org_ids={"https://ror.org/existing"},
            existing_keys={"p1__https://ror.org/existing"},
        ),
    )
    assert m_added == 1
    assert sorted(_membership_org_ids(reconciled, person_id="p1")) == [
        "https://ror.org/existing",
        "https://ror.org/new",
    ]


def test_apply_patch_idempotent_when_membership_already_present():
    reconciled = ReconciledEntities(entities={"persons": [{"id": "p1"}]})
    _seed_membership(reconciled, person_id="p1", ror="https://ror.org/x")
    patch = BioResolverPatch(
        pulse_ror="https://ror.org/x",
        reason="...",
        confidence=0.9,
    )
    m_added, _ = _apply_patch(
        person=reconciled.entities["persons"][0],
        patch=patch,
        **_apply_kwargs(
            reconciled=reconciled,
            existing_org_ids={"https://ror.org/x"},
            existing_keys={"p1__https://ror.org/x"},
        ),
    )
    assert m_added == 0
    assert len(_memberships(reconciled)) == 1


def test_apply_patch_no_op_when_patch_carries_no_ror():
    reconciled = ReconciledEntities(entities={"persons": [{"id": "p1"}]})
    patch = BioResolverPatch(reason="LLM punted", confidence=0.3)
    m_added, _ = _apply_patch(
        person=reconciled.entities["persons"][0],
        patch=patch,
        **_apply_kwargs(reconciled=reconciled, existing_org_ids=set(), existing_keys=set()),
    )
    assert m_added == 0
    assert _memberships(reconciled) == []


# ---------------------------------------------------------------------------
# Per-person LLM failure isolation
# ---------------------------------------------------------------------------


def test_per_person_llm_failure_produces_warning_not_exception():
    reconciled = ReconciledEntities(
        entities={
            "persons": [
                {"id": "good", "_bio": "Senior Research Engineer at DeepMind"},
                {"id": "bad", "_bio": "Engineer at Anywhere"},
            ],
        },
    )
    agent = _StubAgent(
        patches={
            "good": BioResolverPatch(
                pulse_ror="https://ror.org/deepmind",
                reason="...",
                confidence=0.91,
            ),
            "bad": LLMRuntimeError("model returned no JSON"),
        },
    )
    result = _run(reconciled, agent=agent)
    # The good person resolves; the bad person produces a warning but
    # the stage as a whole succeeds.
    assert result.persons_resolved == 1
    assert result.persons_failed == 1
    assert any("bad" in w for w in result.warnings)


def test_unexpected_exception_also_isolated_to_one_person():
    reconciled = ReconciledEntities(
        entities={
            "persons": [
                {"id": "p1", "_bio": "Engineer at SomethingCorp"},
            ],
        },
    )
    agent = _StubAgent(patches={"p1": RuntimeError("network blew up")})
    result = _run(reconciled, agent=agent)
    assert result.persons_resolved == 0
    assert result.persons_failed == 1


# ---------------------------------------------------------------------------
# Provider unavailable
# ---------------------------------------------------------------------------


def test_stage_no_ops_when_provider_missing():
    reconciled = ReconciledEntities(
        entities={"persons": [{"id": "p1", "_bio": "at X"}]},
    )
    agent = _StubAgent(patches={})
    result = asyncio.run(
        run_resolve_bio_to_ror_llm_stage(
            reconciled=reconciled, provider=None, agent=agent,
        ),
    )
    assert result.persons_called == 0
    assert agent.calls == []  # provider gate short-circuits before calling the agent
    assert any("provider unavailable" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Concurrency env var
# ---------------------------------------------------------------------------


def test_concurrency_env_default(monkeypatch):
    monkeypatch.delenv("V2_RESOLVE_BIO_TO_ROR_LLM_CONCURRENCY", raising=False)
    assert _resolve_max_concurrency() == 4


def test_concurrency_env_override(monkeypatch):
    monkeypatch.setenv("V2_RESOLVE_BIO_TO_ROR_LLM_CONCURRENCY", "8")
    assert _resolve_max_concurrency() == 8


def test_concurrency_env_clamped_to_min_1(monkeypatch):
    monkeypatch.setenv("V2_RESOLVE_BIO_TO_ROR_LLM_CONCURRENCY", "0")
    assert _resolve_max_concurrency() == 1


def test_concurrency_env_garbage_falls_back(monkeypatch):
    monkeypatch.setenv("V2_RESOLVE_BIO_TO_ROR_LLM_CONCURRENCY", "lots")
    assert _resolve_max_concurrency() == 4


# ---------------------------------------------------------------------------
# api.py env-flag wiring
# ---------------------------------------------------------------------------


def test_api_env_flag_defaults_on(monkeypatch):
    from git_metadata_extractor.api import _helpers as v2_api

    monkeypatch.delenv("V2_RESOLVE_BIO_TO_ROR_LLM", raising=False)
    assert v2_api._resolve_bio_to_ror_llm_enabled() is True


@pytest.mark.parametrize("value", ["false", "FALSE", "0", "no", "off", "n", "f"])
def test_api_env_flag_recognises_off_values(value, monkeypatch):
    from git_metadata_extractor.api import _helpers as v2_api

    monkeypatch.setenv("V2_RESOLVE_BIO_TO_ROR_LLM", value)
    assert v2_api._resolve_bio_to_ror_llm_enabled() is False


def test_api_constant_and_export_in_place():
    from git_metadata_extractor.api import _helpers as v2_api
    from git_metadata_extractor.pipeline import stages

    assert v2_api.STAGE_RESOLVE_BIO_TO_ROR_LLM == "resolve_bio_to_ror_llm"
    assert callable(stages.run_resolve_bio_to_ror_llm_stage)
    assert "run_resolve_bio_to_ror_llm_stage" in stages.__all__
    assert "BioLLMAffiliationResult" in stages.__all__
