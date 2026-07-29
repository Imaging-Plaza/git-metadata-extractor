"""Tests for the agent-driven `infer_github_handle_parents` stage.

The stage used to fuzzy-search ROR and attach the top-5 token-overlap hits,
stamping the highest-scoring one as the `org:unitOf` parent — which a real
deployment showed scattering 5 unrelated ROR orgs per github handle and
picking the wrong parent 9/10 times. It now hands the candidates to an LLM
selector that picks the one genuine parent (or declines), and inserts only
that one. In rule_based runtime (no selector) a strict deterministic
fallback applies.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from git_metadata_extractor.agents.llm.refiners.ror_parent.agent import RorParentSelectorPatch
from git_metadata_extractor.pipeline.stages.models import AssembledOutput
from git_metadata_extractor.pipeline.stages.ownership_check import (
    _extra_ror_queries_from_metadata,
    _org_context_for_selector,
    infer_github_handle_parents,
)

_EPFL_ROR = "https://ror.org/02s376052"
_NCAR_ROR = "https://ror.org/05cvfcr44"

_EPFL_HIT = {
    "id": _EPFL_ROR,
    "name": "École Polytechnique Fédérale de Lausanne",
    "aliases": [],
    "acronyms": ["EPFL"],
    "types": ["education"],
    "country": {"country_name": "Switzerland"},
}
_NCAR_HIT = {
    "id": _NCAR_ROR,
    "name": "National Center for Atmospheric Research",
    "aliases": [],
    "acronyms": ["NCAR"],
    "types": ["facility"],
    "country": {"country_name": "United States"},
}


class _FakeRORProvider:
    """Returns a fixed hit list for every query."""

    def __init__(self, hits: list[dict[str, Any]]) -> None:
        self._hits = hits

    def search_organizations(self, query: str) -> list[dict[str, Any]]:
        del query
        return [dict(hit) for hit in self._hits]


class _FakeSelector:
    """Stand-in for RorParentSelectorAgent — returns a fixed patch."""

    def __init__(self, patch: RorParentSelectorPatch) -> None:
        self._patch = patch
        self.calls = 0

    async def run(
        self,
        *,
        refiner_input: Any,
        tools: Any = None,
    ) -> RorParentSelectorPatch:
        del tools
        self.calls += 1
        self.last_input = refiner_input
        return self._patch


def _gh_org(handle: str) -> dict[str, Any]:
    return {
        "id": f"https://github.com/{handle}",
        "type": "org:Organization",
        "schema:name": handle,
        "pulse:githubOrganizationHandle": handle,
    }


def _run(assembled: AssembledOutput, **kwargs: Any) -> tuple[AssembledOutput, list[str]]:
    return asyncio.run(infer_github_handle_parents(assembled, **kwargs))


def _inserted_ror_orgs(updated: AssembledOutput) -> list[dict[str, Any]]:
    return [
        e
        for e in updated.related_entities
        if isinstance(e, dict) and e.get("pulse:ror")
    ]


def test_selector_pick_inserts_only_the_chosen_parent() -> None:
    """The fuzzy search surfaces EPFL + NCAR; the selector picks EPFL. Only
    EPFL is inserted — NCAR (and any other noise) never reaches the graph."""
    gh = _gh_org("epfl-lasa")
    selector = _FakeSelector(
        RorParentSelectorPatch(ror_id=_EPFL_ROR, reason="EPFL", confidence=0.95),
    )

    updated, _ = _run(
        AssembledOutput(root_entity=None, related_entities=[gh], excluded_entities=[]),
        providers=SimpleNamespace(ror=_FakeRORProvider([_EPFL_HIT, _NCAR_HIT])),
        parent_selector=selector,
    )

    ror_orgs = _inserted_ror_orgs(updated)
    assert [o["id"] for o in ror_orgs] == [_EPFL_ROR]
    gh_after = next(
        e for e in updated.related_entities if e.get("pulse:githubOrganizationHandle") == "epfl-lasa"
    )
    assert gh_after["org:unitOf"] == [_EPFL_ROR]
    assert selector.calls == 1


def test_selector_decline_leaves_org_standalone() -> None:
    """When the selector declines, no ROR org is inserted and the github org
    keeps no parent — a wrong parent is worse than none."""
    gh = _gh_org("imaging-plaza")
    selector = _FakeSelector(
        RorParentSelectorPatch(ror_id=None, reason="no real parent", confidence=0.0),
    )

    updated, warnings = _run(
        AssembledOutput(root_entity=None, related_entities=[gh], excluded_entities=[]),
        providers=SimpleNamespace(ror=_FakeRORProvider([_NCAR_HIT])),
        parent_selector=selector,
    )

    assert _inserted_ror_orgs(updated) == []
    gh_after = next(
        e for e in updated.related_entities if e.get("pulse:githubOrganizationHandle") == "imaging-plaza"
    )
    assert not gh_after.get("org:unitOf")
    assert any("declined" in w for w in warnings)


def test_selector_low_confidence_pick_is_declined() -> None:
    """A pick below the confidence floor is treated as a decline."""
    gh = _gh_org("epfl-lasa")
    selector = _FakeSelector(
        RorParentSelectorPatch(ror_id=_EPFL_ROR, reason="weak", confidence=0.4),
    )

    updated, _ = _run(
        AssembledOutput(root_entity=None, related_entities=[gh], excluded_entities=[]),
        providers=SimpleNamespace(ror=_FakeRORProvider([_EPFL_HIT, _NCAR_HIT])),
        parent_selector=selector,
    )

    assert _inserted_ror_orgs(updated) == []


def test_rule_based_fallback_accepts_strong_unambiguous_match() -> None:
    """No selector (rule_based runtime): a multi-token, unambiguous winner is
    accepted deterministically."""
    gh = _gh_org("swiss-data-science")
    sdsc_hit = {
        "id": "https://ror.org/02hdt9m26",
        "name": "Swiss Data Science Center",
        "aliases": [],
        "acronyms": ["SDSC"],
        "types": ["facility"],
        "country": {"country_name": "Switzerland"},
    }

    updated, _ = _run(
        AssembledOutput(root_entity=None, related_entities=[gh], excluded_entities=[]),
        providers=SimpleNamespace(ror=_FakeRORProvider([sdsc_hit])),
        parent_selector=None,
    )

    assert [o["id"] for o in _inserted_ror_orgs(updated)] == ["https://ror.org/02hdt9m26"]


def test_rule_based_fallback_rejects_single_token_collision() -> None:
    """No selector: `imaging-plaza` shares only the generic token 'plaza' with
    'Plaza Community Services' (score 1) — below the deterministic bar."""
    gh = _gh_org("imaging-plaza")
    plaza_hit = {
        "id": "https://ror.org/04esec225",
        "name": "Plaza Community Services",
        "aliases": [],
        "acronyms": [],
        "types": ["nonprofit"],
        "country": {"country_name": "United States"},
    }

    updated, _ = _run(
        AssembledOutput(root_entity=None, related_entities=[gh], excluded_entities=[]),
        providers=SimpleNamespace(ror=_FakeRORProvider([plaza_hit])),
        parent_selector=None,
    )

    assert _inserted_ror_orgs(updated) == []


def test_no_ror_provider_is_a_noop() -> None:
    gh = _gh_org("epfl-lasa")
    assembled = AssembledOutput(
        root_entity=None, related_entities=[gh], excluded_entities=[],
    )

    updated, warnings = _run(assembled, providers=SimpleNamespace())

    assert updated is assembled
    assert warnings == []


def test_org_context_carries_the_github_description_and_profile_readme() -> None:
    """The org's GitHub description and profile README (internal `_*` fields)
    — the strongest parent signals — are surfaced for the selector."""
    org = {
        "_description": "Link between EPFL/IC labs and industry",
        "_location": "Lausanne, Switzerland",
        "_blog": "https://c4dt.epfl.ch",
        "_profile_readme": "# C4DT\n\nThe Center for Digital Trust at EPFL.",
    }
    context = _org_context_for_selector(org)
    assert context is not None
    assert context["description"] == "Link between EPFL/IC labs and industry"
    assert context["homepage"] == "https://c4dt.epfl.ch"
    assert context["profile_readme"] == "# C4DT\n\nThe Center for Digital Trust at EPFL."
    assert _org_context_for_selector({}) is None


def test_metadata_mining_surfaces_the_parent_institution() -> None:
    """c4dt's handle ('c4dt') and name ('Center for Digital Trust') never
    surface EPFL — but its description and homepage do."""
    org = {
        "_description": "Center for Digital Trust — Link between EPFL/IC labs and industry",
        "_blog": "https://c4dt.epfl.ch",
    }
    extra = _extra_ror_queries_from_metadata(org)
    assert "EPFL" in extra  # mined from the description acronym
    assert "epfl" in extra  # mined from the homepage domain label
    # Two-letter acronyms ('IC') are too generic to mine.
    assert "IC" not in extra
    assert _extra_ror_queries_from_metadata({}) == []


def test_org_with_existing_ror_is_skipped() -> None:
    """An org that already carries a ROR id is canonical — left untouched."""
    gh = _gh_org("epfl-lasa")
    gh["pulse:ror"] = _EPFL_ROR
    selector = _FakeSelector(
        RorParentSelectorPatch(ror_id=_NCAR_ROR, confidence=0.99),
    )

    updated, _ = _run(
        AssembledOutput(root_entity=None, related_entities=[gh], excluded_entities=[]),
        providers=SimpleNamespace(ror=_FakeRORProvider([_NCAR_HIT])),
        parent_selector=selector,
    )

    # The selector is never consulted and no new org is inserted — the only
    # entity in the graph is the github org we started with.
    assert selector.calls == 0
    assert len(updated.related_entities) == 1
