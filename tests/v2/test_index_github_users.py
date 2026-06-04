"""Unit tests for the github_users index module.

Exercises the full pipe with a faked `GitHubClient` so no network
calls are made: ingest writes a DuckDB row that round-trips through
`fetch_user`, the embed-text composer picks up the right fields, and
the v2 wrapper builds a sane summary on completion.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.index.github_users.embed.pipeline import _row_to_payload, _row_to_text
from src.index.github_users.ingest.users import (
    _record_from_payload,
    ingest_single_user,
)
from src.index.github_users.models import UserRecord
from src.index.github_users.storage.duckdb_store import GitHubUsersStore


class _FakeGitHubClient:
    """Stand-in for `GitHubClient`. Returns the payload it was constructed
    with; raises if asked for a different login than the one it knows."""

    def __init__(self, login: str, payload: dict[str, Any] | None) -> None:
        self._login = login
        self._payload = payload

    def get_user(self, login: str) -> dict[str, Any] | None:
        if login != self._login:
            return None
        return self._payload


@pytest.fixture()
def users_store(tmp_path: Path) -> GitHubUsersStore:
    store = GitHubUsersStore.open(tmp_path / "github_users.duckdb")
    yield store
    store.close()


def test_record_from_payload_normalises_user_card() -> None:
    payload = {
        "id": 123,
        "node_id": "MDQ6VXNlcjEyMw==",
        "login": "alice",
        "name": "Alice Anderson",
        "bio": "Imaging at EPFL",
        "company": "EPFL",
        "blog": "https://alice.example",
        "location": "Lausanne",
        "email": None,
        "twitter_username": None,
        "hireable": True,
        "public_repos": 5,
        "public_gists": 0,
        "followers": 42,
        "following": 7,
        "type": "User",
        "avatar_url": "https://avatars/alice.png",
        "html_url": "https://github.com/alice",
        "created_at": "2018-01-15T09:30:00Z",
        "updated_at": "2024-09-01T12:00:00Z",
    }

    record = _record_from_payload("alice", payload)

    assert record.login == "https://github.com/alice"
    assert record.github_id == 123
    assert record.bio == "Imaging at EPFL"
    assert record.company == "EPFL"
    assert record.account_type == "User"
    assert record.public_repos == 5
    assert record.followers == 42
    assert record.created_at is not None
    assert record.raw == payload


def test_ingest_single_user_round_trips_through_duckdb(
    users_store: GitHubUsersStore,
) -> None:
    payload = {
        "id": 456,
        "login": "bob",
        "name": "Bob Builder",
        "bio": "Open-source pipelines",
        "company": "@SomeOrg",
        "location": "Zurich",
        "blog": "",
        "type": "User",
        "public_repos": 12,
        "followers": 8,
        "created_at": "2020-06-01T00:00:00Z",
    }
    client = _FakeGitHubClient("bob", payload)

    outcome = ingest_single_user(
        config=object(),  # ignored by the function body
        store=users_store,
        client=client,
        login="bob",
    )

    assert outcome == "ingested"
    row = users_store.fetch_user("bob")
    assert row is not None
    assert row["login"] == "https://github.com/bob"
    assert row["bio"] == "Open-source pipelines"
    assert row["company"] == "@SomeOrg"
    assert row["public_repos"] == 12


def test_ingest_single_user_skips_unknown_login(
    users_store: GitHubUsersStore,
) -> None:
    client = _FakeGitHubClient("alice", {"login": "alice", "type": "User"})
    outcome = ingest_single_user(
        config=object(), store=users_store, client=client, login="nobody",
    )
    assert outcome == "skipped_404"
    assert users_store.fetch_user("nobody") is None


def test_ingest_single_user_skips_organization_type(
    users_store: GitHubUsersStore,
) -> None:
    payload = {"login": "epfl-org", "type": "Organization", "id": 999}
    client = _FakeGitHubClient("epfl-org", payload)
    outcome = ingest_single_user(
        config=object(), store=users_store, client=client, login="epfl-org",
    )
    assert outcome == "skipped_org"
    assert users_store.fetch_user("epfl-org") is None


def test_compose_embedding_text_includes_every_signal() -> None:
    row = {
        "login": "alice",
        "name": "Alice Anderson",
        "bio": "Imaging research",
        "company": "EPFL",
        "location": "Lausanne",
        "blog": "https://alice.example",
    }
    text = _row_to_text(row)
    assert "alice" in text
    assert "Alice Anderson" in text
    assert "Imaging research" in text
    assert "EPFL" in text
    assert "Lausanne" in text
    assert "https://alice.example" in text


def test_compose_embedding_text_skips_empty_fields() -> None:
    row = {
        "login": "ghost",
        "name": None,
        "bio": "   ",
        "company": "",
        "location": None,
        "blog": None,
    }
    # Only login should make it through — but the value is still a
    # non-empty single-line string the embed loop can decide to skip
    # via min_card_chars.
    text = _row_to_text(row)
    assert text == "ghost"


def test_payload_strips_to_filterable_signals() -> None:
    row = {
        "login": "alice",
        "github_id": 123,
        "name": "Alice",
        "company": "EPFL",
        "location": "Lausanne",
        "public_repos": 5,
        "followers": 42,
        "html_url": "https://github.com/alice",
        "created_at": None,
        # Fields that should NOT appear in the payload (they're in
        # DuckDB but not relevant for Qdrant filtering):
        "raw": {"big": "blob"},
        "bio": "private bio",
        "email": "secret@example.com",
    }
    payload = _row_to_payload(row)
    assert payload["entity_type"] == "users"
    assert payload["entity_id"] == "alice"
    assert payload["login"] == "alice"
    assert payload["company"] == "EPFL"
    assert "raw" not in payload
    assert "bio" not in payload  # bio is for embedding, not for filtering
    assert "email" not in payload


def test_duckdb_store_count_starts_at_zero(users_store: GitHubUsersStore) -> None:
    assert users_store.count("users") == 0
    record = UserRecord(login="alice", name="Alice", account_type="User")
    users_store.upsert_user(record)
    assert users_store.count("users") == 1


def test_upsert_user_is_idempotent_on_login(
    users_store: GitHubUsersStore,
) -> None:
    users_store.upsert_user(UserRecord(login="alice", name="Alice", followers=10))
    users_store.upsert_user(UserRecord(login="alice", name="Alice (renamed)", followers=11))
    row = users_store.fetch_user("alice")
    assert row is not None
    assert row["name"] == "Alice (renamed)"
    assert row["followers"] == 11
    assert users_store.count("users") == 1
