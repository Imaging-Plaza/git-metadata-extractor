"""Unit tests for the huggingface_users index module."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.index.huggingface_users.embed.pipeline import _row_to_payload, _row_to_text
from src.index.huggingface_users.ingest.users import (
    _record_from_overview,
    ingest_single_user,
)
from src.index.huggingface_users.models import HFUserRecord
from src.index.huggingface_users.storage.duckdb_store import (
    HuggingFaceUsersStore,
)


class _FakeHFClient:
    """Stub for HFClient.namespace_overview returning (kind, overview)."""

    def __init__(self, slug: str, kind: str | None, overview: Any | None) -> None:
        self._slug = slug
        self._kind = kind
        self._overview = overview

    def namespace_overview(self, slug: str) -> tuple[str, Any] | None:
        if slug != self._slug or self._kind is None:
            return None
        return self._kind, self._overview


@pytest.fixture()
def users_store(tmp_path: Path) -> HuggingFaceUsersStore:
    store = HuggingFaceUsersStore.open(tmp_path / "huggingface_users.duckdb")
    yield store
    store.close()


# ---------------------------------------------------------------------------
# overview → HFUserRecord
# ---------------------------------------------------------------------------


def test_record_from_overview_accepts_dict_shape() -> None:
    overview = {
        "fullname": "Yann LeCun",
        "details": "Chief AI Scientist",
        "avatar_url": "https://hf.co/avatars/ylecun.png",
        "num_models": 5,
        "num_datasets": 2,
        "num_spaces": 1,
        "num_followers": 200_000,
    }
    record = _record_from_overview("ylecun", overview)
    assert record.slug == "ylecun"
    assert record.fullname == "Yann LeCun"
    assert record.num_models == 5
    assert record.num_followers == 200_000


def test_record_from_overview_accepts_object_shape() -> None:
    overview = SimpleNamespace(
        fullname="Alice",
        details=None,
        avatar_url=None,
        num_models=0,
        num_datasets=0,
        num_spaces=0,
        num_followers=5,
    )
    record = _record_from_overview("alice", overview)
    assert record.fullname == "Alice"
    assert record.num_followers == 5


def test_record_from_overview_tolerates_camelcase_fields() -> None:
    overview = {"numModels": 3, "numFollowers": 100}
    record = _record_from_overview("o", overview)
    assert record.num_models == 3
    assert record.num_followers == 100


# ---------------------------------------------------------------------------
# DuckDB round-trip
# ---------------------------------------------------------------------------


def test_ingest_single_user_round_trips(users_store: HuggingFaceUsersStore) -> None:
    client = _FakeHFClient(
        "alice", "user",
        SimpleNamespace(
            fullname="Alice A", details="researcher",
            num_models=1, num_datasets=0, num_spaces=0, num_followers=10,
            avatar_url=None,
        ),
    )
    outcome = ingest_single_user(
        config=object(), store=users_store, client=client, slug="alice",
    )
    assert outcome == "ingested"
    row = users_store.fetch_user("alice")
    assert row is not None
    assert row["fullname"] == "Alice A"
    assert row["num_followers"] == 10


def test_ingest_skips_unknown_slug(users_store: HuggingFaceUsersStore) -> None:
    client = _FakeHFClient("alice", "user", SimpleNamespace())
    assert ingest_single_user(
        config=object(), store=users_store, client=client, slug="ghost",
    ) == "skipped_404"


def test_ingest_skips_org_kind(users_store: HuggingFaceUsersStore) -> None:
    """If HF returns kind='org' for a slug we passed to the users
    ingester, route it elsewhere (skip → no DuckDB row)."""
    client = _FakeHFClient("openai", "org", SimpleNamespace(fullname="OpenAI"))
    assert ingest_single_user(
        config=object(), store=users_store, client=client, slug="openai",
    ) == "skipped_org"
    assert users_store.fetch_user("openai") is None


def test_upsert_idempotent(users_store: HuggingFaceUsersStore) -> None:
    users_store.upsert_user(HFUserRecord(slug="a", num_followers=1))
    users_store.upsert_user(HFUserRecord(slug="a", num_followers=99))
    row = users_store.fetch_user("a")
    assert row is not None and row["num_followers"] == 99
    assert users_store.count("users") == 1


# ---------------------------------------------------------------------------
# Embedding text + payload
# ---------------------------------------------------------------------------


def test_row_to_text_combines_slug_and_bio() -> None:
    row = {"slug": "ylecun", "fullname": "Yann LeCun", "details": "Chief AI Scientist"}
    text = _row_to_text(row)
    assert "ylecun" in text
    assert "Yann LeCun" in text
    assert "Chief AI Scientist" in text


def test_row_to_payload_strips_to_filterable_signals() -> None:
    row = {
        "slug": "ylecun",
        "fullname": "Yann LeCun",
        "num_models": 5,
        "num_followers": 200_000,
        # NOT in payload:
        "details": "private bio",
        "raw": {"big": "blob"},
    }
    payload = _row_to_payload(row)
    assert payload["entity_type"] == "users"
    assert payload["entity_id"] == "ylecun"
    assert payload["fullname"] == "Yann LeCun"
    assert "details" not in payload  # bio feeds embedding only
    assert "raw" not in payload
