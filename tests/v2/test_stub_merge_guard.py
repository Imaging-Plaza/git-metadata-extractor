# tests/v2/test_stub_merge_guard.py
"""Bug 07: the `_stub` reference-only marker must not bleed onto a fuller
same-id entity through the generic merge helpers. A merged entity is a stub
only if BOTH sides were stubs.
"""
from __future__ import annotations

from src.v2.pipeline.stages.llm_dedup import _merge_entity_payload
from src.v2.pipeline.stages.output_assembly import _merge_into


# --- _merge_into (output_assembly) ---

def test_merge_into_stub_source_does_not_taint_full_target():
    target = {"id": "x", "schema:name": "Full"}  # not a stub
    _merge_into(target, {"id": "x", "_stub": True, "extra": 1})
    assert "_stub" not in target
    assert target["extra"] == 1  # non-stub fields still merge


def test_merge_into_full_source_clears_target_stub():
    target = {"id": "x", "_stub": True}
    _merge_into(target, {"id": "x", "schema:name": "Full"})  # source not a stub
    assert "_stub" not in target


def test_merge_into_both_stubs_keeps_stub():
    target = {"id": "x", "_stub": True}
    _merge_into(target, {"id": "x", "_stub": True})
    assert target["_stub"] is True


# --- _merge_entity_payload (llm_dedup) ---

def test_merge_payload_stub_candidate_does_not_taint_full_canonical():
    canonical = {"id": "x", "schema:name": "Full"}
    _merge_entity_payload(canonical, {"id": "x", "_stub": True, "extra": "y"})
    assert "_stub" not in canonical
    assert canonical.get("extra") == "y"


def test_merge_payload_full_candidate_clears_canonical_stub():
    canonical = {"id": "x", "_stub": True}
    _merge_entity_payload(canonical, {"id": "x", "schema:name": "Full"})
    assert "_stub" not in canonical


def test_merge_payload_both_stubs_keeps_stub():
    canonical = {"id": "x", "_stub": True}
    _merge_entity_payload(canonical, {"id": "x", "_stub": True})
    assert canonical["_stub"] is True
