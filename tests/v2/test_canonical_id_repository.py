from __future__ import annotations

import uuid
from typing import Any, Callable

from src.v2.canonicalization import resolve_repository_id
from src.v2.validation.schema_validation import StrictSchemaValidator

UUID_V5_VERSION = 5


def test_resolve_repository_id_prefers_github_repository_handle() -> None:
    repository = {
        "identifiers": {
            "pulse:githubRepositoryHandle": "owner/repo",
            "schema:citation": "10.5281/zenodo.1234",
        },
    }

    canonical_id, id_source = resolve_repository_id(repository)

    assert canonical_id == "https://github.com/owner/repo"
    assert id_source == "pulse:githubRepositoryHandle"


def test_resolve_repository_id_uses_doi_when_github_handle_is_missing() -> None:
    repository = {
        "identifiers": {
            "pulse:githubRepositoryHandle": None,
            "schema:citation": "10.5281/zenodo.1234",
        },
    }

    canonical_id, id_source = resolve_repository_id(repository)

    assert canonical_id == "https://doi.org/10.5281/zenodo.1234"
    assert id_source == "schema:citation"


def test_resolve_repository_id_falls_back_to_uuid_v5() -> None:
    repository = {
        "schema:name": "Some Repository",
        "identifiers": {
            "pulse:githubRepositoryHandle": None,
            "schema:citation": None,
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
            "schema:citation": "10.5281/zenodo.1234",
        },
    }

    canonical_id, id_source = resolve_repository_id(repository)

    assert canonical_id == "https://doi.org/10.5281/zenodo.1234"
    assert id_source == "schema:citation"


def test_resolve_repository_id_output_is_strict_enum_compatible(
    load_fixture: Callable[[str, str], Any],
) -> None:
    validator = StrictSchemaValidator()
    repository = load_fixture("schema/strict", "pulse_RepositoryShape")[0]
    repository["id"] = "https://github.com/owner/repo"
    repository["idSource"] = resolve_repository_id(repository)[1]

    result = validator.validate("repository", repository)

    assert result.is_valid is True


def test_resolve_repository_id_canonicalizes_pre_resolved_github_handle() -> None:
    repository = {
        "id": "owner/repo",
        "idSource": "pulse:githubRepositoryHandle",
        "identifiers": {
            "pulse:githubRepositoryHandle": "owner/repo",
        },
    }

    canonical_id, id_source = resolve_repository_id(repository)

    assert canonical_id == "https://github.com/owner/repo"
    assert id_source == "pulse:githubRepositoryHandle"
