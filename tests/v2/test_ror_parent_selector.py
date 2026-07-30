"""Tests for the LLM ROR-parent selector agent.

The selector replaces the old "insert the top-5 token-overlap ROR hits and
stamp the highest-scoring one as the parent" behaviour in
`infer_github_handle_parents`. It must: pick a candidate only at/above the
confidence floor, decline cleanly, and NEVER honour a ROR id the LLM
invented outside the candidate set.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from git_metadata_extractor.agents.llm.refiners.ror_parent.agent import (
    MIN_SELECTION_CONFIDENCE,
    RorCandidate,
    RorParentSelectorAgent,
    RorParentSelectorInput,
    RorParentSelectorPatch,
)
from git_metadata_extractor.agents.llm.runtime import LLMRuntimeError, LLMRuntimeResult

_EPFL_ROR = "https://ror.org/02s376052"
_NCAR_ROR = "https://ror.org/05cvfcr44"


class _FakeRuntime:
    """Returns a fixed payload and records the prompts it was handed."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.system_prompt: str | None = None
        self.user_prompt: str | None = None

    async def run_json_prompt(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        output_type: Any = None,
        tools: Any = None,
    ) -> LLMRuntimeResult:
        del output_type, tools
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt
        return LLMRuntimeResult(
            payload=dict(self._payload),
            model="openai/gpt-test",
            provider="openai",
        )


class _SlowRuntime:
    async def run_json_prompt(self, **_: Any) -> LLMRuntimeResult:
        await asyncio.sleep(0.5)
        return LLMRuntimeResult(payload={}, model="m", provider="p")


def _epfl_lasa_input() -> RorParentSelectorInput:
    """The `epfl-lasa` case from the production report: a Swiss lab whose
    fuzzy search surfaced NCAR (US) as the top-scoring candidate."""
    return RorParentSelectorInput(
        github_handle="epfl-lasa",
        github_org_name="EPFL-LASA",
        candidates=[
            RorCandidate(
                ror_id=_NCAR_ROR,
                name="National Center for Atmospheric Research",
                acronyms=["NCAR"],
                country="United States",
                token_overlap_score=2,
            ),
            RorCandidate(
                ror_id=_EPFL_ROR,
                name="École Polytechnique Fédérale de Lausanne",
                acronyms=["EPFL"],
                country="Switzerland",
                token_overlap_score=1,
            ),
        ],
    )


def test_picks_candidate_above_confidence_floor() -> None:
    runtime = _FakeRuntime(
        {"ror_id": _EPFL_ROR, "reason": "EPFL", "confidence": 0.93},
    )
    agent = RorParentSelectorAgent(llm_runtime=runtime)

    patch = asyncio.run(agent.run(refiner_input=_epfl_lasa_input()))

    assert patch.ror_id == _EPFL_ROR
    assert patch.accepted_ror_id() == _EPFL_ROR
    # The github handle must reach the model so it can reason about geography.
    assert "epfl-lasa" in (runtime.user_prompt or "")


def test_low_confidence_pick_is_not_accepted() -> None:
    runtime = _FakeRuntime(
        {"ror_id": _EPFL_ROR, "reason": "weak", "confidence": 0.55},
    )
    agent = RorParentSelectorAgent(llm_runtime=runtime)

    patch = asyncio.run(agent.run(refiner_input=_epfl_lasa_input()))

    assert patch.ror_id == _EPFL_ROR
    # Below the floor → caller must treat it as a decline.
    assert patch.accepted_ror_id() is None


def test_declines_with_null_ror_id() -> None:
    runtime = _FakeRuntime(
        {"ror_id": None, "reason": "no candidate is the parent", "confidence": 0.0},
    )
    agent = RorParentSelectorAgent(llm_runtime=runtime)

    patch = asyncio.run(agent.run(refiner_input=_epfl_lasa_input()))

    assert patch.ror_id is None
    assert patch.accepted_ror_id() is None


def test_invented_ror_id_is_discarded() -> None:
    """The LLM may only choose from the candidates we fetched. A ROR id that
    is not in the candidate set must never reach the graph."""
    runtime = _FakeRuntime(
        {
            "ror_id": "https://ror.org/deadbeef9",
            "reason": "hallucinated",
            "confidence": 0.99,
        },
    )
    agent = RorParentSelectorAgent(llm_runtime=runtime)

    patch = asyncio.run(agent.run(refiner_input=_epfl_lasa_input()))

    assert patch.ror_id is None
    assert patch.accepted_ror_id() is None
    assert patch.confidence == 0.0


def test_empty_candidates_short_circuits() -> None:
    """No candidates → no LLM call, plain decline."""
    runtime = _FakeRuntime({"ror_id": _EPFL_ROR, "confidence": 0.99})
    agent = RorParentSelectorAgent(llm_runtime=runtime)

    patch = asyncio.run(
        agent.run(
            refiner_input=RorParentSelectorInput(
                github_handle="epfl-lasa",
                candidates=[],
            ),
        ),
    )

    assert patch.accepted_ror_id() is None
    assert runtime.user_prompt is None  # the LLM was never called


def test_unparseable_payload_is_a_decline() -> None:
    runtime = _FakeRuntime({"unexpected": "shape", "confidence": "not-a-number"})
    agent = RorParentSelectorAgent(llm_runtime=runtime)

    patch = asyncio.run(agent.run(refiner_input=_epfl_lasa_input()))

    assert patch.accepted_ror_id() is None


def test_timeout_raises_llm_runtime_error() -> None:
    agent = RorParentSelectorAgent(
        llm_runtime=_SlowRuntime(),
        llm_call_timeout_seconds=0.01,
    )

    with pytest.raises(LLMRuntimeError):
        asyncio.run(agent.run(refiner_input=_epfl_lasa_input()))


def test_patch_accepted_helper_honours_floor() -> None:
    assert (
        RorParentSelectorPatch(
            ror_id=_EPFL_ROR,
            confidence=MIN_SELECTION_CONFIDENCE,
        ).accepted_ror_id()
        == _EPFL_ROR
    )
    assert (
        RorParentSelectorPatch(
            ror_id=_EPFL_ROR,
            confidence=MIN_SELECTION_CONFIDENCE - 0.01,
        ).accepted_ror_id()
        is None
    )
    assert RorParentSelectorPatch(ror_id=None, confidence=1.0).accepted_ror_id() is None
