# tests/v2/test_dropped_affiliations.py
"""Bug 05: an affiliation dropped by the membership evidence floor must keep
the original *text* plus an explicit `unresolved` flag and a `source`, not just
an opaque org id.
"""
from __future__ import annotations

from git_metadata_extractor.pipeline.stages.reconciliation import _normalize_membership_entities


def _run(org_entity: dict) -> dict:
    """Drive one evidence-floor drop and return the stamped person entity."""
    persons_by_id = {"P1": {"schema:name": "Alice", "identifiers": {"pulse:orcid": None}}}
    organizations_by_id = {"O1": org_entity}
    # No role, no dates, no ORCID+ROR anchor -> dropped by the evidence floor.
    memberships = [{"id": "alice__acme"}]
    _normalize_membership_entities(
        memberships,
        person_lookup={"alice": "P1"},
        organization_lookup={"acme": "O1"},
        persons_by_id=persons_by_id,
        organizations_by_id=organizations_by_id,
    )
    return persons_by_id["P1"]


def test_dropped_entry_keeps_text_flag_and_source():
    person = _run({
        "schema:name": "AdaptiveMotorControlLab",
        "idSource": "uuid",
        "identifiers": {"pulse:ror": None},
    })
    dropped = person.get("_dropped_affiliations")
    assert isinstance(dropped, list) and len(dropped) == 1
    entry = dropped[0]
    assert entry["text"] == "AdaptiveMotorControlLab"
    assert entry["unresolved"] is True
    assert entry["source"] == "membership_evidence_floor"
    assert entry["org_id"] == "O1"
    assert entry["membership_id"] == "P1__O1"
    # backward-compatible keys preserved
    assert entry["reason"]


def test_text_recovered_from_original_name_when_no_display_name():
    # Org has only _original_name (schema:name absent) -> org_name would be None,
    # but text must still resolve from _original_name.
    person = _run({
        "_original_name": "AdaptiveMotorControlLab",
        "idSource": "uuid",
        "identifiers": {"pulse:ror": None},
    })
    entry = person["_dropped_affiliations"][0]
    assert entry["text"] == "AdaptiveMotorControlLab"
    assert entry["org_name"] is None
    assert entry["unresolved"] is True
