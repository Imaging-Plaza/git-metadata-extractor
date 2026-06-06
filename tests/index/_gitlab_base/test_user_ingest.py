# tests/index/_gitlab_base/test_user_ingest.py
from __future__ import annotations

import httpx

from src.index._gitlab_base.client import GitLabClient
from src.index._gitlab_base.user_ingest import _user_record_from_payload, ingest_users
from src.index._gitlab_base.user_store import GitLabUserStore

_PAYLOAD = {
    "web_url": "https://gitlab.epfl.ch/jdoe",
    "username": "jdoe",
    "name": "Jane Doe",
    "bio": "Researcher in imaging",
    "organization": "EPFL",
    "job_title": "PI",
    "location": "Lausanne",
    "public_email": "jane@example.org",
}


def test_maps_payload_with_url_id():
    rec = _user_record_from_payload("gitlab.epfl.ch", _PAYLOAD)
    assert rec.user_id == "https://gitlab.epfl.ch/jdoe"
    assert rec.username == "jdoe"
    assert rec.name == "Jane Doe"
    assert rec.bio == "Researcher in imaging"
    assert rec.organization == "EPFL"
    assert rec.job_title == "PI"
    assert rec.location == "Lausanne"
    assert rec.public_email == "jane@example.org"


def test_falls_back_to_iri_when_web_url_missing():
    rec = _user_record_from_payload("gitlab.epfl.ch", {"username": "bob"})
    assert rec.user_id == "https://gitlab.epfl.ch/bob"


def _transport(pages: dict[int, list[dict]]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        body = pages.get(page, [])
        nxt = str(page + 1) if (page + 1) in pages else ""
        return httpx.Response(200, json=body, headers={"X-Next-Page": nxt})
    return httpx.MockTransport(handler)


_EXPECTED_USERS = 2


def test_ingest_users_roundtrip_into_duckdb(tmp_path):
    pages = {
        1: [{"username": "alice", "web_url": "https://gitlab.epfl.ch/alice", "name": "Alice"}],
        2: [{"username": "bob", "web_url": "https://gitlab.epfl.ch/bob", "name": "Bob"}],
    }
    client = GitLabClient(
        host="gitlab.epfl.ch", token=None, transport=_transport(pages),
    )
    store = GitLabUserStore.open(tmp_path / "users.duckdb")
    try:
        result = ingest_users(host="gitlab.epfl.ch", client=client, store=store)
        assert result == {"seen": _EXPECTED_USERS}
        assert store.count("users") == _EXPECTED_USERS
        row = store.fetch_user("https://gitlab.epfl.ch/alice")
        assert row is not None
        assert row["username"] == "alice"
        assert row["name"] == "Alice"
    finally:
        client.close()
        store.close()
