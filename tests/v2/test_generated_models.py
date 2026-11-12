from __future__ import annotations

import json
from pathlib import Path

from git_metadata_extractor.schema.models.strict import PersonModel, RepositoryModel

REPO_ROOT = Path(__file__).resolve().parents[2]
STRICT_FIXTURE_DIR = REPO_ROOT / "tests" / "v2" / "fixtures" / "schema" / "strict"


def _load_fixture_list(file_name: str) -> list[dict[str, object]]:
    payload = json.loads((STRICT_FIXTURE_DIR / file_name).read_text(encoding="utf-8"))
    assert isinstance(payload, list)
    return payload


def test_person_model_validates_person_fixture_payloads() -> None:
    for payload in _load_fixture_list("pulse_PersonShape.json"):
        model = PersonModel.model_validate(payload)
        assert model.root.id == payload["id"]
        assert model.root.model_dump(by_alias=True)["schema:name"] == payload["schema:name"]


def test_repository_model_validates_repository_fixture_payloads() -> None:
    for payload in _load_fixture_list("pulse_RepositoryShape.json"):
        model = RepositoryModel.model_validate(payload)
        assert model.id == payload["id"]
        assert model.model_dump(by_alias=True)["schema:name"] == payload["schema:name"]
