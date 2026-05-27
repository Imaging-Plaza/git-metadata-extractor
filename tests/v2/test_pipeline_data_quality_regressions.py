"""Regression tests for the v1-era data-quality issues #29–#36.

The fixes themselves shipped quietly inside v2 pipeline stages — see
`demote_github_props_to_units`, `_pick_membership_role`, and the owner-stub
synthesis path in `guarantee_repo_author`. The issues remained open because
every one of them carries an explicit "add tests to prevent regression"
task that nobody followed up on. This module fills that gap with
targeted unit tests that exercise the functions directly, without
spinning up the full pipeline.

Issue coverage map:

- #29 / #33 — `pulse:githubOrgFollowers` must live on the github unit,
  not the ROR parent. Covered by `test_demote_*`.
- #30 / #34 — duplicate Memberships differing only in role formatting
  ("Ph.D Student" vs "PhD Student") must collapse cleanly. Covered by
  `test_pick_membership_role_*`.
- #31 / #32 / #35 / #36 — Contribution / Repository without an author
  (deleted GitHub user) must get a synthetic Person stub instead of
  flunking validation. Covered by `test_synthesize_owner_person_stub_*`
  and `test_pick_membership_role_*` is unaffected.
"""

from __future__ import annotations

from typing import Any

from src.v2.pipeline.stages.models import AssembledOutput
from src.v2.pipeline.stages.ownership_check import (
    _synthesize_owner_person_stub,
    demote_github_props_to_units,
)
from src.v2.pipeline.stages.reconciliation import (
    _normalize_role_value,
    _pick_membership_role,
)

ORG_TYPE = "org:Organization"


def _make_ror_parent(
    *,
    ror_id: str,
    handle: str,
    followers: int | None,
    has_unit: list[str] | None,
) -> dict[str, Any]:
    parent: dict[str, Any] = {
        "id": ror_id,
        "type": ORG_TYPE,
        "schema:name": "Some Parent Org",
        "pulse:githubOrganizationHandle": handle,
    }
    if followers is not None:
        parent["pulse:githubOrgFollowers"] = followers
    if has_unit is not None:
        parent["org:hasUnit"] = [{"@id": u} for u in has_unit]
    return parent


def _make_github_unit(*, handle: str, followers: int | None = None) -> dict[str, Any]:
    unit: dict[str, Any] = {
        "id": f"https://github.com/{handle}",
        "type": ORG_TYPE,
        "schema:name": handle,
        "pulse:githubOrganizationHandle": handle,
    }
    if followers is not None:
        unit["pulse:githubOrgFollowers"] = followers
    return unit


# ----------------------------------------------------------------------------
# #29 / #33 — github org followers must land on the unit, not the parent
# ----------------------------------------------------------------------------


def test_demote_moves_followers_from_ror_parent_to_matching_unit():
    parent = _make_ror_parent(
        ror_id="https://ror.org/0070nx673",
        handle="InteractiveComputerGraphics",
        followers=364,
        has_unit=["https://github.com/InteractiveComputerGraphics"],
    )
    unit = _make_github_unit(handle="InteractiveComputerGraphics", followers=None)
    assembled = AssembledOutput(root_entity=parent, related_entities=[unit])

    updated, demote_warnings = demote_github_props_to_units(assembled)

    new_parent = updated.root_entity
    new_unit = next(
        e for e in updated.related_entities
        if isinstance(e, dict)
        and e.get("id") == "https://github.com/InteractiveComputerGraphics"
    )
    # Follower count migrates to the unit; parent loses GitHub-derived props.
    assert new_unit["pulse:githubOrgFollowers"] == 364
    assert new_parent["pulse:githubOrgFollowers"] is None
    assert new_parent["pulse:githubOrganizationHandle"] is None
    # A warning is emitted so the operator can see the demotion happened.
    assert any("Demoted GitHub-derived properties" in w for w in demote_warnings)


def test_demote_never_overwrites_existing_unit_follower_count():
    parent = _make_ror_parent(
        ror_id="https://ror.org/0070nx673",
        handle="InteractiveComputerGraphics",
        followers=999,  # parent has a (likely stale) value
        has_unit=["https://github.com/InteractiveComputerGraphics"],
    )
    unit = _make_github_unit(handle="InteractiveComputerGraphics", followers=364)
    assembled = AssembledOutput(root_entity=parent, related_entities=[unit])

    updated, _ = demote_github_props_to_units(assembled)

    new_unit = next(
        e for e in updated.related_entities
        if isinstance(e, dict)
        and e.get("id") == "https://github.com/InteractiveComputerGraphics"
    )
    # The unit's own (authoritative) value is preserved.
    assert new_unit["pulse:githubOrgFollowers"] == 364


def test_demote_synthesizes_unit_when_parent_handle_has_no_matching_unit():
    """Case 2 from the function docstring: handle on parent, no matching child."""
    parent = _make_ror_parent(
        ror_id="https://ror.org/02s376052",
        handle="GeoEnergyLab-EPFL",
        followers=12,
        has_unit=[],  # parent carries handle but never linked a unit
    )
    assembled = AssembledOutput(root_entity=parent, related_entities=[])

    updated, warnings = demote_github_props_to_units(assembled)

    synthesized = [
        e for e in updated.related_entities
        if isinstance(e, dict)
        and e.get("id") == "https://github.com/GeoEnergyLab-EPFL"
    ]
    assert len(synthesized) == 1
    assert synthesized[0]["pulse:githubOrgFollowers"] == 12
    assert synthesized[0]["pulse:githubOrganizationHandle"] == "GeoEnergyLab-EPFL"
    assert synthesized[0]["org:unitOf"] == ["https://ror.org/02s376052"]
    # And the parent's hasUnit list now references the new stub.
    parent_has_unit = updated.root_entity["org:hasUnit"]
    assert {u.get("@id") if isinstance(u, dict) else u for u in parent_has_unit} == {
        "https://github.com/GeoEnergyLab-EPFL",
    }
    assert any("Synthesized github-only org unit" in w for w in warnings)


def test_demote_is_noop_when_parent_has_no_github_handle():
    """A ROR org with no GitHub presence at all must be left alone."""
    parent: dict[str, Any] = {
        "id": "https://ror.org/0001234",
        "type": ORG_TYPE,
        "schema:name": "Pure ROR Org",
    }
    assembled = AssembledOutput(root_entity=parent, related_entities=[])

    updated, warnings = demote_github_props_to_units(assembled)

    assert warnings == []
    assert updated.root_entity == parent


# ----------------------------------------------------------------------------
# #30 / #34 — duplicate Memberships with formatting-only role differences
# ----------------------------------------------------------------------------


def test_normalize_role_value_collapses_punctuation_and_case():
    """`_normalize_role_value` is the equivalence key for role dedup."""
    assert _normalize_role_value("Ph.D Student") == "phd student"
    assert _normalize_role_value("PhD Student") == "phd student"
    assert _normalize_role_value("phd  student") == "phd student"
    assert _normalize_role_value("  Ph.D.  Student  ") == "phd student"
    # Falsy / non-string inputs collapse to empty so they sort below real roles.
    assert _normalize_role_value(None) == ""
    assert _normalize_role_value(42) == ""


def test_pick_membership_role_keeps_longest_when_normalized_equal():
    """When every duplicate normalises to the same role, prefer the richer spelling."""
    roles = [
        ("Ph.D Student", "2013-01-01", "2018-01-01"),
        ("PhD Student", "2013-01-01", "2018-01-01"),
        ("phd student", "2013-01-01", "2018-01-01"),
    ]
    # "Ph.D Student" is the longest — punctuation and dot survive.
    assert _pick_membership_role(roles) == "Ph.D Student"


def test_pick_membership_role_picks_most_recent_when_roles_differ():
    roles = [
        ("Postdoc", "2018-01-01", "2020-12-31"),
        ("PhD Student", "2013-01-01", "2018-01-01"),
    ]
    # End-date ranking → "Postdoc" wins because 2020 > 2018.
    assert _pick_membership_role(roles) == "Postdoc"


def test_pick_membership_role_returns_none_when_no_valid_roles():
    assert _pick_membership_role([(None, None, None)]) is None
    assert _pick_membership_role([("   ", "2018", "2020")]) is None
    assert _pick_membership_role([]) is None


# ----------------------------------------------------------------------------
# #31 / #32 / #35 / #36 — synthesise a Person when the github owner is gone
# ----------------------------------------------------------------------------


def test_synthesize_owner_person_stub_is_a_valid_person_shape():
    stub = _synthesize_owner_person_stub("RossComputerGuy")

    assert stub["id"] == "https://github.com/RossComputerGuy"
    assert stub["type"] == "schema:Person"
    assert stub["shacl"] == "pulse:PersonShape"
    assert stub["schema:name"] == "RossComputerGuy"
    # PersonShape requires pulse:githubUsername among the identifiers.
    assert stub["identifiers"]["pulse:githubUsername"] == "RossComputerGuy"
    assert stub["idSource"] == "pulse:githubUsername"
    # ORCID / Infoscience identifiers are explicitly nullable on a stub —
    # we never invent those.
    assert stub["identifiers"]["pulse:orcid"] is None
    assert stub["identifiers"]["pulse:infosciencePersonIdentifier"] is None
    # And every stub has its own uuid so two synthesised stubs never collide.
    assert isinstance(stub["identifiers"]["uuid"], str)
    assert stub["identifiers"]["uuid"]


def test_two_stubs_have_distinct_uuids():
    a = _synthesize_owner_person_stub("alice-handle")
    b = _synthesize_owner_person_stub("bob-handle")
    assert a["identifiers"]["uuid"] != b["identifiers"]["uuid"]
