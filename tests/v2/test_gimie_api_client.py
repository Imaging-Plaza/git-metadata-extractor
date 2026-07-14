"""Tests for the gimie-api sidecar client + the provider seam.

No real network — a fake session is injected, and env is monkeypatched.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from git_metadata_extractor.providers.gimie_extract import extract_gimie
from git_metadata_extractor.providers.gimie_api_client import (
    extract_gimie_via_api,
    gimie_api_base,
)
from git_metadata_extractor.providers.github_provider import RealGitHubProvider

if TYPE_CHECKING:
    import pytest

# The sidecar has no JSON-LD route (task brief 11) — the client fetches TTL
# and converts with rdflib, mirroring the in-process reference serialization.
_TTL = """\
@prefix schema: <http://schema.org/> .

<https://github.com/acme/tool> a schema:SoftwareSourceCode ;
    schema:name "tool" ;
    schema:description "A fine tool." .
"""
_BASE = "http://gme-gimie-api:15400"


class _FakeResp:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        if isinstance(self._payload, ValueError):
            raise self._payload
        return self._payload


class _FakeSession:
    def __init__(self, resp: Any) -> None:
        self._resp = resp
        self.urls: list[str] = []

    def get(self, url: str, timeout: float | None = None) -> Any:  # noqa: ARG002
        self.urls.append(url)
        if isinstance(self._resp, Exception):
            raise self._resp
        return self._resp


def _enable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIMIE_API_URL", _BASE)


# ---------------------------------------------------------------------------
# gimie_api_base
# ---------------------------------------------------------------------------


def test_gimie_api_base_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GIMIE_API_URL", raising=False)
    assert gimie_api_base() is None


def test_gimie_api_base_strips_trailing_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIMIE_API_URL", _BASE + "/")
    assert gimie_api_base() == _BASE


# ---------------------------------------------------------------------------
# extract_gimie_via_api
# ---------------------------------------------------------------------------


def test_extract_unset_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GIMIE_API_URL", raising=False)
    assert extract_gimie_via_api("https://github.com/acme/tool", session=object()) is None


def test_extract_jsonld_converts_ttl_via_the_ttl_route(monkeypatch: pytest.MonkeyPatch) -> None:
    # json-ld requests go to /gimie/ttl/ (the sidecar's only machine route)
    # and are converted to rdflib's expanded JSON-LD — the same shape the
    # in-process extractor produced.
    _enable(monkeypatch)
    session = _FakeSession(_FakeResp(200, {"link": "x", "output": _TTL}))
    out = extract_gimie_via_api("https://github.com/acme/tool", session=session)
    assert session.urls == [f"{_BASE}/gimie/ttl/https://github.com/acme/tool"]
    assert isinstance(out, list)
    node = next(n for n in out if n.get("@id") == "https://github.com/acme/tool")
    assert any("SoftwareSourceCode" in t for t in node["@type"])
    assert node["http://schema.org/description"] == [{"@value": "A fine tool."}]


def test_extract_jsonld_node_survives_repository_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The expanded list must be consumable by the provider's node finder.
    from git_metadata_extractor.providers.github_provider import _extract_repository_node

    _enable(monkeypatch)
    session = _FakeSession(_FakeResp(200, {"output": _TTL}))
    out = extract_gimie_via_api("https://github.com/acme/tool", session=session)
    node = _extract_repository_node(out)
    assert node.get("@id") == "https://github.com/acme/tool"


def test_extract_non_string_output_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    session = _FakeSession(_FakeResp(200, {"output": {"unexpected": "dict"}}))
    assert extract_gimie_via_api("https://github.com/acme/tool", session=session) is None


def test_extract_error_string_as_http_200_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    # gimie-api's error contract: HTTP 200 with `output` = an error message,
    # which does not parse as Turtle.
    _enable(monkeypatch)
    session = _FakeSession(_FakeResp(200, {"output": "Exception: repo not found"}))
    assert extract_gimie_via_api("https://github.com/acme/missing", session=session) is None


def test_extract_non_200_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    session = _FakeSession(_FakeResp(500, None))
    assert extract_gimie_via_api("https://github.com/acme/tool", session=session) is None


def test_extract_transport_error_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    session = _FakeSession(TimeoutError("boom"))
    assert extract_gimie_via_api("https://github.com/acme/tool", session=session) is None


def test_extract_ttl_returns_string(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    session = _FakeSession(_FakeResp(200, {"output": "@prefix schema: <...> ."}))
    out = extract_gimie_via_api("https://github.com/acme/tool", "ttl", session=session)
    assert out == "@prefix schema: <...> ."
    assert session.urls == [f"{_BASE}/gimie/ttl/https://github.com/acme/tool"]


# ---------------------------------------------------------------------------
# provider seam + the extract_gimie intermediate
# ---------------------------------------------------------------------------


def test_seam_returns_intermediate(monkeypatch: pytest.MonkeyPatch) -> None:
    # The seam always resolves to the single `extract_gimie` intermediate;
    # API-vs-in-process routing happens inside it (next test).
    monkeypatch.delenv("GIMIE_API_URL", raising=False)
    assert RealGitHubProvider()._resolve_gimie_extractor() is extract_gimie  # noqa: SLF001
    _enable(monkeypatch)
    assert RealGitHubProvider()._resolve_gimie_extractor() is extract_gimie  # noqa: SLF001


def test_intermediate_routes_to_api_when_url_set(monkeypatch: pytest.MonkeyPatch) -> None:
    # With GIMIE_API_URL set, extract_gimie delegates to the sidecar client and
    # never touches in-process gimie.
    _enable(monkeypatch)
    sentinel = {"@graph": []}
    monkeypatch.setattr(
        "git_metadata_extractor.providers.gimie_api_client.extract_gimie_via_api",
        lambda full_path, fmt="json-ld": sentinel,  # noqa: ARG005
    )
    assert extract_gimie("https://github.com/acme/tool") is sentinel


def test_seam_explicit_extractor_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    sentinel = object()
    provider = RealGitHubProvider(gimie_extractor=sentinel)  # type: ignore[arg-type]
    assert provider._resolve_gimie_extractor() is sentinel  # noqa: SLF001
