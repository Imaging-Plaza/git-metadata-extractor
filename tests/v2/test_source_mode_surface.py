"""Source selection is validated and bound to each request before provider creation."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from types import SimpleNamespace
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.requests import Request as StarletteRequest

from git_metadata_extractor import config, dependencies
from git_metadata_extractor.api_models import V2ExtractRequest
from git_metadata_extractor.experiments import __main__ as cli
from git_metadata_extractor.experiments.builtins import default_catalog
from git_metadata_extractor.experiments.execution import execution_manifest
from git_metadata_extractor.experiments.models import PipelineProfile

HTTP_UNPROCESSABLE_ENTITY = 422


def test_source_mode_environment_default_and_validation(monkeypatch):
    monkeypatch.delenv("V2_SOURCE_MODE", raising=False)
    assert config.resolve_source_mode() == "direct"
    monkeypatch.setenv("V2_SOURCE_MODE", "snapshot")
    assert config.resolve_source_mode() == "snapshot"
    assert config.resolve_source_mode("direct") == "direct"
    assert config.V2Config().V2_SOURCE_MODE == "snapshot"
    monkeypatch.setenv("V2_SOURCE_MODE", "fallback")
    with pytest.raises(ValueError, match="V2_SOURCE_MODE"):
        config.V2Config()


@pytest.mark.parametrize(
    ("model", "required"),
    [
        (V2ExtractRequest, {"source_url": "octocat/Hello-World"}),
        (PipelineProfile, {"name": "trial"}),
    ],
)
def test_source_mode_models_reject_unknown_modes(model, required):
    assert model(**required).source_mode is None
    assert model(**required, source_mode="snapshot").source_mode == "snapshot"
    with pytest.raises(ValidationError):
        model(**required, source_mode="fallback")


def test_profile_source_modes_have_distinct_fingerprints():
    catalog = default_catalog()
    direct = catalog.manifest(PipelineProfile(name="trial", source_mode="direct"))
    snapshot = catalog.manifest(PipelineProfile(name="trial", source_mode="snapshot"))
    assert direct["fingerprint"] != snapshot["fingerprint"]


def _request(app, method, mode):
    body = json.dumps(
        {"source_url": "octocat/Hello-World", "source_mode": mode},
    ).encode()

    async def receive():
        await asyncio.sleep(0)
        return {"type": "http.request", "body": body, "more_body": False}

    return StarletteRequest(
        {
            "type": "http",
            "method": method,
            "app": app,
            "path": "/v2/extract"
            if method == "POST"
            else "/v2/extract/octocat/Hello-World",
            "path_params": {},
            "headers": [],
            "query_string": f"source_mode={mode}".encode() if method == "GET" else b"",
        },
        receive=receive,
    )


def test_concurrent_source_selection_is_request_local_and_post_body_survives(
    monkeypatch,
):
    monkeypatch.setenv("V2_SOURCE_MODE", "direct")
    monkeypatch.setattr(
        dependencies,
        "build_provider_resources",
        lambda _state, **kw: SimpleNamespace(bundle=kw["source_mode"]),
    )
    app = FastAPI()
    requests = [
        _request(app, method, mode)
        for method, mode in (
            ("GET", "snapshot"),
            ("POST", "direct"),
            ("POST", "snapshot"),
            ("GET", "direct"),
        )
    ]

    async def run():
        return await asyncio.gather(
            *(dependencies.get_provider_set(r) for r in requests),
        )

    assert asyncio.run(run()) == ["snapshot", "direct", "snapshot", "direct"]
    assert asyncio.run(requests[2].json())["source_mode"] == "snapshot"
    assert os.environ["V2_SOURCE_MODE"] == "direct"


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_invalid_source_selection_is_422_before_provider_construction(
    monkeypatch, method,
):
    def unexpected_factory(*_args, **_kwargs):
        pytest.fail("Invalid source mode must not construct providers")

    monkeypatch.setattr(dependencies, "build_provider_resources", unexpected_factory)
    app = FastAPI()

    @app.api_route("/v2/extract", methods=["GET", "POST"])
    async def route(
        request: Request,
        providers: Annotated[object, Depends(dependencies.get_provider_set)],
    ):
        return {"mode": request.state.v2_source_mode, "providers": providers}

    with TestClient(app) as client:
        response = (
            client.get("/v2/extract?source_mode=fallback")
            if method == "GET"
            else client.post("/v2/extract", json={"source_mode": "fallback"})
        )
    assert response.status_code == HTTP_UNPROCESSABLE_ENTITY
    assert response.json()["detail"][0]["loc"] == [
        "query" if method == "GET" else "body",
        "source_mode",
    ]


def test_execution_manifest_freezes_environment_fallback(monkeypatch):
    profile = PipelineProfile(name="trial")
    catalog = default_catalog()
    monkeypatch.setenv("V2_SOURCE_MODE", "direct")
    direct = execution_manifest(catalog, profile, "live")
    monkeypatch.setenv("V2_SOURCE_MODE", "snapshot")
    snapshot = execution_manifest(catalog, profile, "live")
    explicit = execution_manifest(
        catalog, PipelineProfile(name="trial", source_mode="direct"), "live",
    )
    assert direct["profile"]["source_mode"] == "direct"
    assert snapshot["profile"]["source_mode"] == "snapshot"
    assert direct["fingerprint"] != snapshot["fingerprint"]
    assert direct["fingerprint"] == explicit["fingerprint"]
    assert profile.source_mode is None


@pytest.mark.parametrize("body", [b"not JSON", b""])
def test_source_selection_preserves_422_for_invalid_post_bodies(monkeypatch, body):
    def unexpected_factory(*_args, **_kwargs):
        pytest.fail("Invalid body must not construct providers")

    monkeypatch.setattr(dependencies, "build_provider_resources", unexpected_factory)
    app = FastAPI()

    @app.post("/v2/extract")
    async def route(
        payload: V2ExtractRequest,
        providers: Annotated[object, Depends(dependencies.get_provider_set)],
    ):
        return {"payload": payload.model_dump(), "providers": providers}

    with TestClient(app) as client:
        response = client.post("/v2/extract", content=body, headers={"Content-Type": "text/plain"})
    assert response.status_code == HTTP_UNPROCESSABLE_ENTITY


def test_benchmark_cli_overrides_profile_source_mode(tmp_path, monkeypatch):
    profile = tmp_path / "profile.yaml"
    profile.write_text("name: trial\nsource_mode: direct\n", encoding="utf-8")
    corpus = tmp_path / "corpus.json"
    corpus.write_text('{"cases": []}', encoding="utf-8")
    selected = []

    async def benchmark(profiles, _cases, **_kwargs):
        selected.extend(p.source_mode for p in profiles)
        return {"profiles": [], "trials": []}

    monkeypatch.setattr(cli, "run_benchmark", benchmark)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "experiments",
            "benchmark",
            "--profile",
            str(profile),
            "--corpus",
            str(corpus),
            "--output",
            str(tmp_path / "out"),
            "--source-mode",
            "snapshot",
        ],
    )
    assert cli.main() == 0
    assert selected == ["snapshot"]
