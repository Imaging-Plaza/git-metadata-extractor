from __future__ import annotations

import uuid

from src.v2.canonicalization import resolve_repository_id

UUID_V5_VERSION = 5


def test_resolve_repository_id_prefers_github_repository_handle() -> None:
    repository = {
        "identifiers": {
            "pulse:githubRepositoryHandle": "owner/repo",
            "schema:identifier": "10.5281/zenodo.1234",
        },
    }

    canonical_id, id_source = resolve_repository_id(repository)

    assert canonical_id == "https://github.com/owner/repo"
    assert id_source == "githubRepositoryHandle"


def test_resolve_repository_id_uses_doi_when_github_handle_is_missing() -> None:
    repository = {
        "identifiers": {
            "pulse:githubRepositoryHandle": None,
            "schema:identifier": "10.5281/zenodo.1234",
        },
    }

    canonical_id, id_source = resolve_repository_id(repository)

    assert canonical_id == "https://doi.org/10.5281/zenodo.1234"
    assert id_source == "doi"


def test_resolve_repository_id_falls_back_to_uuid_v5() -> None:
    repository = {
        "schema:name": "Some Repository",
        "identifiers": {
            "pulse:githubRepositoryHandle": None,
            "schema:identifier": None,
        },
    }

    canonical_id, id_source = resolve_repository_id(repository)

    parsed = uuid.UUID(canonical_id)
    assert parsed.version == UUID_V5_VERSION
    assert id_source == "uuid"


def test_resolve_repository_id_validates_github_handle_shape() -> None:
    repository = {
        "identifiers": {
            "pulse:githubRepositoryHandle": "owner/repo/extra",
            "schema:identifier": "10.5281/zenodo.1234",
        },
    }

    canonical_id, id_source = resolve_repository_id(repository)

    assert canonical_id == "https://doi.org/10.5281/zenodo.1234"
    assert id_source == "doi"
