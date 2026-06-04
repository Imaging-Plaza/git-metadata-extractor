# tests/index/_gitlab_base/test_client.py
from __future__ import annotations

import httpx

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
