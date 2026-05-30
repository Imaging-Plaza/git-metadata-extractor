"""Unit tests for the huggingface_organizations index module."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.index.huggingface_organizations.embed.pipeline import (
    _row_to_payload,
    _row_to_text,
)
from src.index.huggingface_organizations.ingest.organizations import (
    _record_from_overview,
    ingest_single_organization,
)
from src.index.huggingface_organizations.models import HFOrgRecord
from src.index.huggingface_organizations.storage.duckdb_store import (
    HuggingFaceOrganizationsStore,
)


class _FakeHFClient:
    def __init__(self, slug: str, kind: str | None, overview: Any | None) -> None:
        self._slug = slug
        self._kind = kind
        self._overview = overview

    def namespace_overview(self, slug: str) -> tuple[str, Any] | None:
        if slug != self._slug or self._kind is None:
            return None
        return self._kind, self._overview


@pytest.fixture()
def orgs_store(tmp_path: Path) -> HuggingFaceOrganizationsStore:
    store = HuggingFaceOrganizationsStore.open(
        tmp_path / "huggingface_organizations.duckdb",
    )
    yield store
    store.close()


# ---------------------------------------------------------------------------
# overview → HFOrgRecord
# ---------------------------------------------------------------------------


def test_record_from_overview_populates_org_fields() -> None:
    overview = {
        "fullname": "OpenAI",
        "details": "Research lab",
        "num_models": 50,
        "num_datasets": 10,
        "num_spaces": 5,
        "num_followers": 1_000_000,
    }
    record = _record_from_overview("openai", overview)
    assert record.slug == "openai"
    assert record.fullname == "OpenAI"
    assert record.num_models == 50
    assert record.num_followers == 1_000_000


# ---------------------------------------------------------------------------
# DuckDB round-trip
# ---------------------------------------------------------------------------


def test_ingest_single_org_round_trips(orgs_store: HuggingFaceOrganizationsStore) -> None:
    client = _FakeHFClient(
        "epfl", "org",
        SimpleNamespace(
            fullname="EPFL", details="École Polytechnique",
            num_models=15, num_datasets=8, num_spaces=3, num_followers=500,
        ),
    )
    outcome = ingest_single_organization(
        config=object(), store=orgs_store, client=client, slug="epfl",
    )
    assert outcome == "ingested"
    row = orgs_store.fetch_organization("epfl")
    assert row is not None
    assert row["fullname"] == "EPFL"
    assert row["num_models"] == 15


def test_ingest_skips_unknown_slug(orgs_store: HuggingFaceOrganizationsStore) -> None:
    client = _FakeHFClient("epfl", "org", SimpleNamespace())
    assert ingest_single_organization(
        config=object(), store=orgs_store, client=client, slug="ghost",
    ) == "skipped_404"


def test_ingest_skips_user_kind(orgs_store: HuggingFaceOrganizationsStore) -> None:
    """If HF says the slug is a user, route to the sibling
    huggingface_users module instead."""
    client = _FakeHFClient("alice", "user", SimpleNamespace(fullname="Alice"))
    assert ingest_single_organization(
        config=object(), store=orgs_store, client=client, slug="alice",
    ) == "skipped_user"
    assert orgs_store.fetch_organization("alice") is None


def test_upsert_idempotent(orgs_store: HuggingFaceOrganizationsStore) -> None:
    orgs_store.upsert_organization(HFOrgRecord(slug="o", num_models=1))
    orgs_store.upsert_organization(HFOrgRecord(slug="o", num_models=99))
    row = orgs_store.fetch_organization("o")
    assert row is not None and row["num_models"] == 99
    assert orgs_store.count("organizations") == 1


# ---------------------------------------------------------------------------
# Embedding text + payload
# ---------------------------------------------------------------------------


def test_row_to_text_combines_slug_and_details() -> None:
    row = {"slug": "openai", "fullname": "OpenAI", "details": "AI research lab"}
    text = _row_to_text(row)
    assert "openai" in text
    assert "OpenAI" in text
    assert "AI research lab" in text


def test_row_to_payload_strips_to_filterable_signals() -> None:
    row = {
        "slug": "openai",
        "fullname": "OpenAI",
        "num_models": 50,
        "num_followers": 1_000_000,
        # NOT in payload:
        "details": "long bio",
        "raw": {"big": "blob"},
    }
    payload = _row_to_payload(row)
    assert payload["entity_type"] == "organizations"
    assert payload["entity_id"] == "openai"
    assert payload["num_followers"] == 1_000_000
    assert "details" not in payload
    assert "raw" not in payload
