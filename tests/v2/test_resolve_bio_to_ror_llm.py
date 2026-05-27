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

from src.v2.agents.llm.refiners.bio_resolver import (
    BioResolverInput,
    BioResolverPatch,
)
from src.v2.agents.llm.runtime import LLMRuntimeError
from src.v2.pipeline.stages.models import ReconciledEntities
from src.v2.pipeline.stages.resolve_bio_to_ror_llm import (
    BioLLMAffiliationResult,
    _apply_patch,
    _resolve_max_concurrency,
    run_resolve_bio_to_ror_llm_stage,
)
from src.v2.pipeline.stages.resolve_company_to_ror import SCHEMA_AFFILIATION


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
    """Skip persons already affiliated, and skip persons with no bio /
    orcid / readme — the LLM has nothing to quote."""
    reconciled = ReconciledEntities(
        entities={
            "persons": [
                {"id": "already", "_bio": "Engineer at X", SCHEMA_AFFILIATION: "https://ror.org/x"},
                {"id": "empty"},  # no signal at all
                {"id": "with-bio", "_bio": "Research Engineer at DeepMind"},
                {"id": "with-readme", "_profile_readme": "# About me\nI work at MIT."},
            ],
        },
    )
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
            "persons": [
                {"id": "p1", "_bio": "...", SCHEMA_AFFILIATION: "https://ror.org/x"},
            ],
        },
    )
    agent = _StubAgent(patches={})
    result = _run(reconciled, agent=agent)
    assert result.persons_called == 0
    assert agent.calls == []


# ---------------------------------------------------------------------------
# Patch application
# ---------------------------------------------------------------------------


def test_high_confidence_ror_stamps_affiliation():
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
    assert reconciled.entities["persons"][0][SCHEMA_AFFILIATION] == "https://ror.org/deepmind"
    assert result.persons_resolved == 1
    assert result.persons_failed == 0


def test_low_confidence_ror_is_dropped_at_apply_time():
    """Even if the agent leaked a ROR with confidence < 0.7 past its
    own internal floor, the stage-level `_apply_patch` re-checks."""
    person: dict[str, Any] = {"id": "p1"}
    patch = BioResolverPatch(
        pulse_ror="https://ror.org/x",
        reason="vague",
        confidence=0.5,
    )
    assert _apply_patch(person, patch) is False
    assert SCHEMA_AFFILIATION not in person


def test_apply_patch_merges_with_existing_string_into_list():
    person: dict[str, Any] = {
        "id": "p1",
        SCHEMA_AFFILIATION: "https://ror.org/existing",
    }
    patch = BioResolverPatch(
        pulse_ror="https://ror.org/new",
        reason="From bio: ...",
        confidence=0.9,
    )
    assert _apply_patch(person, patch) is True
    assert person[SCHEMA_AFFILIATION] == [
        "https://ror.org/existing",
        "https://ror.org/new",
    ]


def test_apply_patch_appends_to_existing_list_without_dup():
    person: dict[str, Any] = {
        "id": "p1",
        SCHEMA_AFFILIATION: ["https://ror.org/a", "https://ror.org/b"],
    }
    patch = BioResolverPatch(
        pulse_ror="https://ror.org/b",
        reason="...",
        confidence=0.9,
    )
    # Already in the list → no-op.
    assert _apply_patch(person, patch) is False
    assert person[SCHEMA_AFFILIATION] == ["https://ror.org/a", "https://ror.org/b"]


def test_apply_patch_no_op_on_idempotent_string_match():
    person: dict[str, Any] = {
        "id": "p1",
        SCHEMA_AFFILIATION: "https://ror.org/x",
    }
    patch = BioResolverPatch(
        pulse_ror="https://ror.org/x",
        reason="...",
        confidence=0.9,
    )
    assert _apply_patch(person, patch) is False


def test_apply_patch_no_op_when_patch_carries_no_ror():
    person: dict[str, Any] = {"id": "p1"}
    patch = BioResolverPatch(reason="LLM punted", confidence=0.3)
    assert _apply_patch(person, patch) is False
    assert SCHEMA_AFFILIATION not in person


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
    from src.v2 import api as v2_api

    monkeypatch.delenv("V2_RESOLVE_BIO_TO_ROR_LLM", raising=False)
    assert v2_api._resolve_bio_to_ror_llm_enabled() is True


@pytest.mark.parametrize("value", ["false", "FALSE", "0", "no", "off", "n", "f"])
def test_api_env_flag_recognises_off_values(value, monkeypatch):
    from src.v2 import api as v2_api

    monkeypatch.setenv("V2_RESOLVE_BIO_TO_ROR_LLM", value)
    assert v2_api._resolve_bio_to_ror_llm_enabled() is False


def test_api_constant_and_export_in_place():
    from src.v2 import api as v2_api
    from src.v2.pipeline import stages

    assert v2_api.STAGE_RESOLVE_BIO_TO_ROR_LLM == "resolve_bio_to_ror_llm"
    assert callable(stages.run_resolve_bio_to_ror_llm_stage)
    assert "run_resolve_bio_to_ror_llm_stage" in stages.__all__
    assert "BioLLMAffiliationResult" in stages.__all__
