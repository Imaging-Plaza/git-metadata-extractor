# tests/index/_gitlab_base/test_project_ingest.py
from __future__ import annotations
from src.index._gitlab_base.project_ingest import _project_record_from_payload

_PAYLOAD = {
    "web_url": "https://gitlab.epfl.ch/grp/proj",
    "path_with_namespace": "grp/proj",
    "name": "proj",
    "description": "a project",
    "visibility": "public",
    "topics": ["ml", "rust"],
    "star_count": 5,
    "forks_count": 1,
    "default_branch": "main",
    "namespace": {"full_path": "grp"},
    "forked_from_project": {"web_url": "https://gitlab.epfl.ch/up/stream"},
}


def test_maps_payload_with_url_id():
    rec = _project_record_from_payload("gitlab.epfl.ch", _PAYLOAD)
    assert rec.project_id == "https://gitlab.epfl.ch/grp/proj"
    assert rec.full_path == "grp/proj"
    assert rec.topics == ["ml", "rust"]
    assert rec.is_fork is True
    assert rec.forked_from == "https://gitlab.epfl.ch/up/stream"
    assert rec.namespace == "grp"


def test_falls_back_to_iri_when_web_url_missing():
    rec = _project_record_from_payload("gitlab.epfl.ch", {"path_with_namespace": "a/b"})
    assert rec.project_id == "https://gitlab.epfl.ch/a/b"
