# tests/index/_gitlab_base/test_group_ingest.py
from __future__ import annotations

from src.index._gitlab_base.group_ingest import _group_record_from_payload

_PAYLOAD = {
    "web_url": "https://gitlab.epfl.ch/groups/mygrp",
    "full_path": "mygrp",
    "name": "My Group",
    "description": "A test group",
    "visibility": "public",
}


def test_maps_payload_with_url_id():
    rec = _group_record_from_payload("gitlab.epfl.ch", _PAYLOAD)
    assert rec.group_id == "https://gitlab.epfl.ch/groups/mygrp"
    assert rec.full_path == "mygrp"
    assert rec.name == "My Group"
    assert rec.description == "A test group"
    assert rec.visibility == "public"


def test_falls_back_to_iri_when_web_url_missing():
    rec = _group_record_from_payload("gitlab.epfl.ch", {"full_path": "a/subgroup"})
    assert rec.group_id == "https://gitlab.epfl.ch/groups/a/subgroup"
