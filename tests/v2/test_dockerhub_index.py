"""Tests for the dockerhub index module.

Covers identifier normalisation, the Docker Hub REST client (thinning,
404/error degradation, anonymous), DuckDB storage round-trip, ingest →
record mapping, and the embed/agent wiring (composite text, payload).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from src.index.dockerhub.config import DockerhubIndexConfig, load_config
from src.index.dockerhub.embed.pipeline import (
    DOCKERHUB_COLLECTION,
    _row_to_chunks,
    _row_to_payload,
)
from src.index.dockerhub.ingest.dockerhub_client import DockerHubClient
from src.index.dockerhub.ingest.repos import ingest_single_image, normalize_repo_id
from src.index.dockerhub.models import DockerhubRepoRecord
from src.index.dockerhub.storage.duckdb_store import DockerhubStore


class _FakeResponse:
    def __init__(self, *, status_code: int, payload: Any = None, raise_json: bool = False) -> None:
        self.status_code = status_code
        self._payload = payload
        self._raise_json = raise_json

    def json(self) -> Any:
        if self._raise_json:
            raise ValueError("not json")
        return self._payload


def _patch_get(*responses: Any, side_effect: Any = None):
    target = "src.index.dockerhub.ingest.dockerhub_client.requests.get"
    if side_effect is not None:
        return patch(target, side_effect=side_effect)
    if len(responses) == 1:
        return patch(target, return_value=responses[0])
    return patch(target, side_effect=list(responses))


def _client(tmp_path: Path) -> DockerHubClient:
    return DockerHubClient(cache_path=tmp_path / "p.db")


# --------------------------------------------------------------------------
# normalize_repo_id
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("grafana/grafana", ("grafana", "grafana")),
        ("python", ("library", "python")),
        ("library/redis", ("library", "redis")),
        ("_/postgres", ("library", "postgres")),
        ("docker.io/library/nginx:latest", ("library", "nginx")),
        ("registry-1.docker.io/grafana/loki:2.9", ("grafana", "loki")),
        ("https://hub.docker.com/r/grafana/loki", ("grafana", "loki")),
        ("https://hub.docker.com/_/postgres", ("library", "postgres")),
        ("bitnami/postgresql:16", ("bitnami", "postgresql")),
    ],
)
def test_normalize_repo_id(ref: str, expected: tuple[str, str]) -> None:
    assert normalize_repo_id(ref) == expected


def test_normalize_repo_id_rejects_empty() -> None:
    with pytest.raises(ValueError):
        normalize_repo_id("   ")


# --------------------------------------------------------------------------
# DockerHubClient
# --------------------------------------------------------------------------


def test_get_repository_returns_payload(tmp_path: Path) -> None:
    payload = {"namespace": "grafana", "name": "grafana", "pull_count": 5, "star_count": 9}
    with _patch_get(_FakeResponse(status_code=200, payload=payload)):
        out = _client(tmp_path).get_repository("grafana", "grafana")
    assert out == payload


def test_get_repository_none_on_404(tmp_path: Path) -> None:
    with _patch_get(_FakeResponse(status_code=404, payload={"message": "not found"})):
        assert _client(tmp_path).get_repository("nope", "nope") is None


def test_get_repository_none_on_error_and_bad_json(tmp_path: Path) -> None:
    with _patch_get(_FakeResponse(status_code=500, payload={})):
        assert _client(tmp_path).get_repository("a", "b") is None
    with _patch_get(_FakeResponse(status_code=200, raise_json=True)):
        assert _client(tmp_path).get_repository("a", "b") is None
    with _patch_get(side_effect=ConnectionError("boom")):
        assert _client(tmp_path).get_repository("a", "b") is None


def test_get_tags_thins_and_caps(tmp_path: Path) -> None:
    payload = {"results": [{"name": "latest"}, {"name": "1.0"}, {"bad": "x"}, {"name": "0.9"}]}
    with _patch_get(_FakeResponse(status_code=200, payload=payload)):
        tags = _client(tmp_path).get_tags("grafana", "grafana", limit=2)
    assert tags == ["latest", "1.0"]


def test_get_tags_empty_on_error(tmp_path: Path) -> None:
    with _patch_get(_FakeResponse(status_code=404, payload={})):
        assert _client(tmp_path).get_tags("a", "b") == []


# --------------------------------------------------------------------------
# Storage round-trip
# --------------------------------------------------------------------------


def test_store_upsert_fetch_and_stream(tmp_path: Path) -> None:
    store = DockerhubStore.open(tmp_path / "dh.duckdb")
    store.upsert_image(DockerhubRepoRecord(
        repo_id="library/python", namespace="library", name="python",
        description="Python is an interpreted language", full_description="# Python\nDocs.",
        is_official=True, star_count=10000, pull_count=10_000_000_000,
        tags=["3.12", "latest"],
    ))
    row = store.fetch_image("library/python")
    assert row is not None
    assert row["repo_id"] == "library/python"
    assert row["is_official"] is True
    assert row["pull_count"] == 10_000_000_000
    assert store.count("images") == 1
    assert len(list(store.stream_rows_for_embedding("images"))) == 1
    store.close()


# --------------------------------------------------------------------------
# ingest_single_image
# --------------------------------------------------------------------------


def test_ingest_single_image_persists(tmp_path: Path) -> None:
    cfg = load_config()
    store = DockerhubStore.open(tmp_path / "dh.duckdb")
    client = _client(tmp_path)
    repo_payload = {
        "namespace": "grafana", "name": "grafana",
        "description": "Observability", "full_description": "# Grafana",
        "star_count": 2000, "pull_count": 1_000_000_000,
        "is_private": False, "status": 1,
        "last_updated": "2026-01-02T00:00:00Z",
    }
    tags_payload = {"results": [{"name": "latest"}, {"name": "11.0.0"}]}
    with _patch_get(
        _FakeResponse(status_code=200, payload=repo_payload),
        _FakeResponse(status_code=200, payload=tags_payload),
    ):
        outcome = ingest_single_image(
            config=cfg, store=store, client=client, image_ref="grafana/grafana",
        )
    assert outcome == "ingested"
    # v3.0.0: stored under the canonical Docker Hub URL id.
    row = store.fetch_image("https://hub.docker.com/r/grafana/grafana")
    assert row["tags"] == '["latest", "11.0.0"]' or row["tags"] == ["latest", "11.0.0"]
    assert row["pull_count"] == 1_000_000_000
    store.close()


def test_ingest_single_image_skips_404(tmp_path: Path) -> None:
    cfg = load_config()
    store = DockerhubStore.open(tmp_path / "dh.duckdb")
    client = _client(tmp_path)
    with _patch_get(_FakeResponse(status_code=404, payload={"message": "x"})):
        outcome = ingest_single_image(
            config=cfg, store=store, client=client, image_ref="ghost/missing",
        )
    assert outcome == "skipped_404"
    assert store.count("images") == 0
    store.close()


def test_ingest_official_bare_name_maps_to_library(tmp_path: Path) -> None:
    cfg = load_config()
    store = DockerhubStore.open(tmp_path / "dh.duckdb")
    client = _client(tmp_path)
    with _patch_get(
        _FakeResponse(status_code=200, payload={"namespace": "library", "name": "redis"}),
        _FakeResponse(status_code=200, payload={"results": []}),
    ):
        ingest_single_image(config=cfg, store=store, client=client, image_ref="redis")
    # official bare name -> library namespace -> canonical /_/ URL id.
    row = store.fetch_image("https://hub.docker.com/_/redis")
    assert row is not None
    assert row["is_official"] is True
    store.close()


# --------------------------------------------------------------------------
# Embed helpers
# --------------------------------------------------------------------------


def test_row_to_chunks_skips_short_cards() -> None:
    short = {"repo_id": "a/b", "description": "hi", "full_description": ""}
    assert _row_to_chunks(short, chunk_tokens=512, overlap=64, min_card_chars=64,
                          full_description_max_bytes=1000) == []


def test_row_to_chunks_builds_from_description_and_readme() -> None:
    row = {"repo_id": "grafana/grafana", "description": "Observability platform",
           "full_description": "# Grafana\n" + ("dashboards. " * 50)}
    chunks = _row_to_chunks(row, chunk_tokens=512, overlap=64, min_card_chars=64,
                            full_description_max_bytes=100_000)
    assert chunks
    assert "grafana/grafana" in chunks[0].text


def test_row_to_payload_shape() -> None:
    row = {"repo_id": "grafana/grafana", "namespace": "grafana", "name": "grafana",
           "is_official": False, "star_count": 9, "pull_count": 100,
           "tags": '["latest"]', "last_updated": None}
    p = _row_to_payload(row)
    assert p["entity_type"] == "images"
    assert p["repo_id"] == "grafana/grafana"
    assert p["image"] == "docker.io/grafana/grafana"
    assert p["tags"] == ["latest"]


def test_collection_name() -> None:
    assert DOCKERHUB_COLLECTION == "dockerhub"
