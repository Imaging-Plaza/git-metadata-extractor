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


def _project_transport(
    projects: list[dict], members_by_pid: dict[str, list[dict]],
) -> httpx.MockTransport:
    """Path-aware transport: serves /projects and /projects/:id/members/all,
    and hard-fails if the admin-only /users endpoint is ever requested (Bug 11)."""
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        page = int(request.url.params.get("page", "1"))
        if path.endswith("/users"):
            msg = "admin /users endpoint must not be called"
            raise AssertionError(msg)
        if path.endswith("/projects"):
            body = projects if page == 1 else []
        elif path.endswith("/members/all"):
            pid = path.split("/projects/")[1].split("/members")[0]
            body = members_by_pid.get(pid, []) if page == 1 else []
        else:
            body = []
        return httpx.Response(200, json=body, headers={"X-Next-Page": ""})
    return httpx.MockTransport(handler)


def test_iter_public_users_derives_from_owners_and_members_deduped():
    # Bug 11: users come from public projects' owners + members, not admin /users.
    projects = [
        {"id": 1, "owner": {"id": 10, "username": "alice"}},
        {"id": 2},  # group-namespace project: no owner
    ]
    members = {
        "1": [{"id": 20, "username": "bob"}],
        "2": [{"id": 20, "username": "bob"}, {"id": 30, "username": "carol"}],
    }
    client = GitLabClient(
        host="gitlab.epfl.ch", token=None,
        transport=_project_transport(projects, members),
    )
    try:
        users = list(client.iter_public_users())
    finally:
        client.close()
    # alice (owner of p1) + bob (member of p1, deduped against p2) + carol (p2)
    assert [u["id"] for u in users] == [10, 20, 30]


def test_ingest_users_roundtrip_into_duckdb(tmp_path):
    projects = [{
        "id": 1,
        "owner": {
            "id": 10, "username": "alice",
            "web_url": "https://gitlab.epfl.ch/alice", "name": "Alice",
        },
    }]
    members = {"1": [{
        "id": 20, "username": "bob",
        "web_url": "https://gitlab.epfl.ch/bob", "name": "Bob",
    }]}
    client = GitLabClient(
        host="gitlab.epfl.ch", token=None,
        transport=_project_transport(projects, members),
    )
    store = GitLabUserStore.open(tmp_path / "users.duckdb")
    try:
        result = ingest_users(host="gitlab.epfl.ch", client=client, store=store)
        assert result == {"seen": _EXPECTED_USERS}  # alice (owner) + bob (member)
        assert store.count("users") == _EXPECTED_USERS
        row = store.fetch_user("https://gitlab.epfl.ch/alice")
        assert row is not None
        assert row["username"] == "alice"
        assert row["name"] == "Alice"
    finally:
        client.close()
        store.close()
