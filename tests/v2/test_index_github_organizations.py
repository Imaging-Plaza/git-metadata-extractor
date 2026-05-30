"""Unit tests for the github_organizations index module."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.index.github_organizations.embed.pipeline import (
    _row_to_payload,
    _row_to_text,
)
from src.index.github_organizations.ingest.organizations import (
    _record_from_payload,
    ingest_single_organization,
)
from src.index.github_organizations.models import OrgRecord
from src.index.github_organizations.storage.duckdb_store import (
    GitHubOrganizationsStore,
)


class _FakeGitHubClient:
    """Stand-in for `GitHubClient.get_organization`."""

    def __init__(self, login: str, payload: dict[str, Any] | None) -> None:
        self._login = login
        self._payload = payload

    def get_organization(self, login: str) -> dict[str, Any] | None:
        if login != self._login:
            return None
        return self._payload


@pytest.fixture()
def orgs_store(tmp_path: Path) -> GitHubOrganizationsStore:
    store = GitHubOrganizationsStore.open(tmp_path / "github_organizations.duckdb")
    yield store
    store.close()


def test_record_from_payload_normalises_org_card() -> None:
    payload = {
        "id": 789,
        "login": "EPFL-ENAC",
        "name": "EPFL School of Architecture, Civil and Environmental Engineering",
        "description": "ENAC at EPFL",
        "blog": "https://www.epfl.ch/schools/enac/",
        "location": "Lausanne, Switzerland",
        "email": None,
        "twitter_username": None,
        "company": None,
        "public_repos": 42,
        "public_gists": 0,
        "followers": 350,
        "following": 0,
        "is_verified": True,
        "type": "Organization",
        "avatar_url": "https://avatars/EPFL-ENAC.png",
        "html_url": "https://github.com/EPFL-ENAC",
        "created_at": "2015-04-20T10:00:00Z",
        "updated_at": "2024-09-01T12:00:00Z",
    }

    record = _record_from_payload("EPFL-ENAC", payload)

    assert record.login == "EPFL-ENAC"
    assert record.github_id == 789
    assert record.description == "ENAC at EPFL"
    assert record.account_type == "Organization"
    assert record.public_repos == 42
    assert record.followers == 350
    assert record.is_verified is True
    assert record.created_at is not None


def test_ingest_single_organization_round_trips_through_duckdb(
    orgs_store: GitHubOrganizationsStore,
) -> None:
    payload = {
        "id": 123,
        "login": "Imaging-Plaza",
        "name": "Imaging Plaza",
        "description": "Research imaging tools and pipelines",
        "blog": "",
        "location": "Lausanne",
        "type": "Organization",
        "public_repos": 18,
        "followers": 60,
        "created_at": "2022-03-15T09:30:00Z",
    }
    client = _FakeGitHubClient("Imaging-Plaza", payload)

    outcome = ingest_single_organization(
        config=object(), store=orgs_store, client=client, login="Imaging-Plaza",
    )

    assert outcome == "ingested"
    row = orgs_store.fetch_organization("Imaging-Plaza")
    assert row is not None
    assert row["login"] == "Imaging-Plaza"
    assert row["name"] == "Imaging Plaza"
    assert row["description"] == "Research imaging tools and pipelines"
    assert row["public_repos"] == 18


def test_ingest_single_organization_skips_unknown_login(
    orgs_store: GitHubOrganizationsStore,
) -> None:
    client = _FakeGitHubClient(
        "EPFL-ENAC",
        {"login": "EPFL-ENAC", "type": "Organization"},
    )
    outcome = ingest_single_organization(
        config=object(), store=orgs_store, client=client, login="nobody",
    )
    assert outcome == "skipped_404"
    assert orgs_store.fetch_organization("nobody") is None


def test_ingest_single_organization_skips_user_type(
    orgs_store: GitHubOrganizationsStore,
) -> None:
    payload = {"login": "alice", "type": "User", "id": 1}
    client = _FakeGitHubClient("alice", payload)
    outcome = ingest_single_organization(
        config=object(), store=orgs_store, client=client, login="alice",
    )
    assert outcome == "skipped_user"
    assert orgs_store.fetch_organization("alice") is None


def test_compose_embedding_text_includes_org_specific_signals() -> None:
    row = {
        "login": "EPFL-ENAC",
        "name": "EPFL School of Architecture, Civil and Environmental Engineering",
        "description": "Research at the intersection of architecture and the environment",
        "location": "Lausanne",
        "blog": "https://www.epfl.ch/schools/enac/",
    }
    text = _row_to_text(row)
    assert "EPFL-ENAC" in text
    assert "Architecture" in text
    assert "Lausanne" in text
    assert "schools/enac" in text


def test_compose_embedding_text_skips_empty_fields() -> None:
    row = {
        "login": "minimal-org",
        "name": "",
        "description": "  ",
        "location": None,
        "blog": None,
    }
    text = _row_to_text(row)
    assert text == "minimal-org"


def test_payload_strips_to_filterable_signals() -> None:
    row = {
        "login": "EPFL-ENAC",
        "github_id": 789,
        "name": "EPFL ENAC",
        "location": "Lausanne",
        "public_repos": 42,
        "followers": 350,
        "is_verified": True,
        "html_url": "https://github.com/EPFL-ENAC",
        "created_at": None,
        # NOT in payload (DuckDB only):
        "raw": {"big": "blob"},
        "description": "private description",
        "email": "secret@example.com",
    }
    payload = _row_to_payload(row)
    assert payload["entity_type"] == "organizations"
    assert payload["entity_id"] == "EPFL-ENAC"
    assert payload["login"] == "EPFL-ENAC"
    assert payload["is_verified"] is True
    assert "raw" not in payload
    assert "description" not in payload  # description feeds embedding only
    assert "email" not in payload


def test_duckdb_store_count_starts_at_zero(
    orgs_store: GitHubOrganizationsStore,
) -> None:
    assert orgs_store.count("organizations") == 0
    record = OrgRecord(
        login="EPFL-ENAC",
        name="EPFL ENAC",
        account_type="Organization",
    )
    orgs_store.upsert_organization(record)
    assert orgs_store.count("organizations") == 1


def test_upsert_organization_is_idempotent_on_login(
    orgs_store: GitHubOrganizationsStore,
) -> None:
    orgs_store.upsert_organization(
        OrgRecord(login="EPFL-ENAC", name="ENAC", followers=100),
    )
    orgs_store.upsert_organization(
        OrgRecord(login="EPFL-ENAC", name="ENAC (renamed)", followers=101),
    )
    row = orgs_store.fetch_organization("EPFL-ENAC")
    assert row is not None
    assert row["name"] == "ENAC (renamed)"
    assert row["followers"] == 101
    assert orgs_store.count("organizations") == 1
