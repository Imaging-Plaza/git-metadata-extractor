"""Tier C (LLM parent selector) modes: apply / shadow / off.

`V2_ROR_PARENT_AGENT_MODE` gates whether the agent's pick is applied:
- apply (default): the agent decides (when wired).
- shadow: the agent runs and its pick is LOGGED against the deterministic
  outcome, but the deterministic pick is used — lets operators measure
  agreement on the hard region/sub-entity tail before trusting the agent.
- off: the agent never runs; deterministic only.

Cases use a shortlist where the deterministic Tier-B rule abstains (a tie) so
the agent's pick visibly differs from what gets applied.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.v2.pipeline.stages.ownership_check import _select_ror_parent


class _Patch:
    def __init__(self, ror_id: str | None, confidence: float = 0.9, reason: str = "geo") -> None:
        self._ror_id = ror_id
        self.confidence = confidence
        self.reason = reason

    def accepted_ror_id(self) -> str | None:
        return self._ror_id


class _StubAgent:
    def __init__(self, ror_id: str | None) -> None:
        self._ror_id = ror_id
        self.ran = 0

    async def run(self, *, refiner_input: Any) -> _Patch:
        self.ran += 1
        return _Patch(self._ror_id)


def _hit(ror_id: str, name: str) -> dict:
    return {"id": ror_id, "name": name, "aliases": [], "acronyms": [], "links": [], "external_ids": {}}


# Two distinctive candidates ('acme'), tied token score -> deterministic abstains;
# no domain / exact-name -> Tier A doesn't fire, so the selection path is exercised.
_SHORTLIST = [
    (2, _hit("ror:us", "Acme Holdings (United States)")),
    (2, _hit("ror:uk", "Acme Holdings (United Kingdom)")),
]


def _select(agent: Any, warnings: list[str]) -> Any:
    return asyncio.run(
        _select_ror_parent(
            handle="acme-corp", org_name="Acme Corp", shortlist=_SHORTLIST,
            parent_selector=agent, org_context=None, warnings=warnings,
        ),
    )


def test_apply_uses_agent_pick(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V2_ROR_PARENT_AGENT_MODE", "apply")
    agent = _StubAgent("ror:uk")
    pick = _select(agent, [])
    assert agent.ran == 1
    assert pick is not None and pick["id"] == "ror:uk"
    assert pick["_ror_match_tier"] == "B_llm"


def test_shadow_runs_agent_but_uses_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V2_ROR_PARENT_AGENT_MODE", "shadow")
    agent = _StubAgent("ror:uk")
    warnings: list[str] = []
    pick = _select(agent, warnings)
    assert agent.ran == 1  # ran for observation
    assert pick is None  # deterministic abstained on the tie; agent pick NOT applied
    shadow = [w for w in warnings if "agent-shadow" in w]
    assert shadow and "would pick 'ror:uk'" in shadow[0] and "agree=False" in shadow[0]


def test_off_never_runs_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V2_ROR_PARENT_AGENT_MODE", "off")
    agent = _StubAgent("ror:uk")
    pick = _select(agent, [])
    assert agent.ran == 0
    assert pick is None  # deterministic only, and it abstains on the tie


def test_default_mode_is_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("V2_ROR_PARENT_AGENT_MODE", raising=False)
    agent = _StubAgent("ror:uk")
    pick = _select(agent, [])
    assert agent.ran == 1 and pick is not None and pick["id"] == "ror:uk"
