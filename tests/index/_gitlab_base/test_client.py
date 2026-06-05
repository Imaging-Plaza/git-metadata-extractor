# tests/index/_gitlab_base/test_client.py
from __future__ import annotations

import httpx

import src.index._gitlab_base.client as client_mod
from src.index._gitlab_base.client import GitLabClient


def _transport(pages: dict[int, list[dict]]):
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        body = pages.get(page, [])
        nxt = str(page + 1) if (page + 1) in pages else ""
        return httpx.Response(200, json=body, headers={"X-Next-Page": nxt})
    return httpx.MockTransport(handler)


def test_iter_public_projects_paginates():
    pages = {1: [{"id": 1, "web_url": "https://gl/a"}], 2: [{"id": 2, "web_url": "https://gl/b"}]}
    client = GitLabClient(host="gitlab.epfl.ch", token=None, transport=_transport(pages))
    got = list(client.iter_public_projects())
    assert [p["id"] for p in got] == [1, 2]


def test_sends_token_header_when_present():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("PRIVATE-TOKEN")
        return httpx.Response(200, json=[], headers={"X-Next-Page": ""})

    client = GitLabClient(host="gitlab.epfl.ch", token="abc", transport=httpx.MockTransport(handler))  # noqa: S106
    list(client.iter_public_projects())
    assert seen["auth"] == "abc"


_EXPECTED_RETRY_CALLS = 2


def test_iter_public_groups_paginates():
    pages = {1: [{"id": 10, "web_url": "https://gl/groups/a"}], 2: [{"id": 20, "web_url": "https://gl/groups/b"}]}
    client = GitLabClient(host="gitlab.epfl.ch", token=None, transport=_transport(pages))
    got = list(client.iter_public_groups())
    assert [p["id"] for p in got] == [10, 20]


def test_get_retries_on_429(monkeypatch):
    monkeypatch.setattr(client_mod.time, "sleep", lambda *_: None)
    calls = {"n": 0}
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json=[], headers={"Retry-After": "0", "X-Next-Page": ""})
        return httpx.Response(200, json=[{"id": 1}], headers={"X-Next-Page": ""})
    client = GitLabClient(host="gitlab.epfl.ch", token=None, transport=httpx.MockTransport(handler))
    got = list(client.iter_public_projects())
    assert [p["id"] for p in got] == [1]
    assert calls["n"] == _EXPECTED_RETRY_CALLS
