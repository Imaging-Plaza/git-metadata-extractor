"""github_repos `repo_id` is the canonical GitHub URL (v3.0.0 index ids).

All index ids that have a canonical URL are stored as that URL — consistent
with the zenodo/openalex/ror stores and the extract pipeline's pulse:* github
IRIs. `owner` / `name` stay bare (decomposed fields).
"""

from __future__ import annotations

from src.index.github_repos.ingest.repos import _record_from_payload


def test_repo_id_is_canonical_url() -> None:
    rec = _record_from_payload(
        full_name="pallets/click",
        repo_payload={"owner": {"login": "pallets"}, "name": "click"},
        languages={"Python": 100},
        contributors=[],
        readme_text=None,
        readme_path=None,
    )
    assert rec.repo_id == "https://github.com/pallets/click"
    assert rec.owner == "pallets"  # bare, unchanged
    assert rec.name == "click"


def test_repo_id_idempotent_if_already_url() -> None:
    rec = _record_from_payload(
        full_name="https://github.com/pallets/click",
        repo_payload={"name": "click"},
        languages={},
        contributors=[],
        readme_text=None,
        readme_path=None,
    )
    assert rec.repo_id == "https://github.com/pallets/click"
