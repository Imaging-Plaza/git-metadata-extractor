"""Tests for the gimie-api sidecar client + the provider seam.

No real network — a fake session is injected, and env is monkeypatched.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from src.v1.gimie_utils.gimie_methods import extract_gimie
from src.v2.ingest.providers.gimie_api_client import (
    extract_gimie_via_api,
    gimie_api_base,
)
from src.v2.ingest.providers.github_provider import RealGitHubProvider

if TYPE_CHECKING:
    import pytest

_JSONLD = {
    "@context": {"schema": "https://schema.org/"},
    "@graph": [{"@id": "https://github.com/acme/tool", "@type": "schema:SoftwareSourceCode"}],
}
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


def test_extract_success_parses_output_string(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    session = _FakeSession(_FakeResp(200, {"link": "x", "output": json.dumps(_JSONLD)}))
    out = extract_gimie_via_api("https://github.com/acme/tool", session=session)
    assert out == _JSONLD
    assert session.urls == [f"{_BASE}/gimie/jsonld/https://github.com/acme/tool"]


def test_extract_accepts_already_parsed_output(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    session = _FakeSession(_FakeResp(200, {"output": _JSONLD}))
    assert extract_gimie_via_api("https://github.com/acme/tool", session=session) == _JSONLD


def test_extract_error_string_as_http_200_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    # gimie-api's error contract: HTTP 200 with `output` = an error message.
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
        "src.v2.ingest.providers.gimie_api_client.extract_gimie_via_api",
        lambda full_path, fmt="json-ld": sentinel,  # noqa: ARG005
    )
    assert extract_gimie("https://github.com/acme/tool") is sentinel


def test_seam_explicit_extractor_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    sentinel = object()
    provider = RealGitHubProvider(gimie_extractor=sentinel)  # type: ignore[arg-type]
    assert provider._resolve_gimie_extractor() is sentinel  # noqa: SLF001
