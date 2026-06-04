"""HTTP-level tests for the reset endpoints.

`DELETE /v2/indices/<provider>/reset` and
`DELETE /v2/indices/reset-all`. Pairs with `test_indices_reset.py`
which covers the underlying function — here we exercise the FastAPI
surface (status codes, query flags, error shapes).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.v2.api import v2_router

HTTP_OK = 200
HTTP_NOT_FOUND = 404

TEST_API_TOKEN = "test-api-token"  # noqa: S105
_AUTH_HEADERS = {"Authorization": f"Bearer {TEST_API_TOKEN}"}


def _build_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(v2_router)
    return app


def _request(app: FastAPI, method: str, path: str, **kwargs: Any) -> tuple[int, Any]:
    async def _run() -> tuple[int, Any]:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.request(
                method, path, headers=_AUTH_HEADERS, **kwargs,
            )
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            body = resp.text
        return resp.status_code, body

    return asyncio.run(_run())


class _FakeQdrantClient:
    def __init__(self, existing: list[str]) -> None:
        self._existing = set(existing)
        self.deleted: list[str] = []

    def get_collections(self):  # noqa: ANN201
        return SimpleNamespace(
            collections=[SimpleNamespace(name=n) for n in self._existing],
        )

    def delete_collection(self, *, collection_name: str) -> None:
        self.deleted.append(collection_name)
        self._existing.discard(collection_name)


def _fake_config():  # noqa: ANN201
    return SimpleNamespace(
        qdrant=SimpleNamespace(url="http://x", api_key=None, prefer_grpc=False),
        paths=SimpleNamespace(cache_db_path=None),
    )


# ---------------------------------------------------------------------------
# DELETE /v2/indices/{provider}/reset
# ---------------------------------------------------------------------------


def test_reset_endpoint_drops_qdrant_and_deletes_duckdb(tmp_path, monkeypatch) -> None:
    """End-to-end: bootstrap a real DuckDB for huggingface_models, hit
    the DELETE endpoint, verify both the file and the (fake) Qdrant
    collection are gone."""
    monkeypatch.setenv("INDEX_DATA_DIR", str(tmp_path))

    from src.index.huggingface_models.models import ModelRecord
    from src.index.huggingface_models.paths import get_huggingface_models_paths
    from src.index.huggingface_models.storage.duckdb_store import (
        HuggingFaceModelsStore,
    )

    paths = get_huggingface_models_paths()
    db_path = paths.duckdb_path
    store = HuggingFaceModelsStore.open(db_path)
    store.upsert_model(ModelRecord(repo_id="org/m", downloads=10))
    store.close()
    assert db_path.exists()

    fake = _FakeQdrantClient(existing=["huggingface_models"])
    app = _build_test_app()
    with patch("qdrant_client.QdrantClient", return_value=fake), patch(
        "src.index.huggingface_models.config.load_config",
        return_value=_fake_config(),
    ):
        code, body = _request(app, "DELETE", "/v2/indices/huggingface_models/reset")

    assert code == HTTP_OK
    assert body["provider"] == "huggingface_models"
    assert body["duckdb_deleted"] is True
    assert body["duckdb_bytes_reclaimed"] > 0
    assert body["qdrant_collections_dropped"] == ["huggingface_models"]
    assert body["qdrant_skipped"] is False
    assert body["cache_cleared"] is False
    assert not db_path.exists()
    assert "huggingface_models" in fake.deleted


def test_reset_endpoint_honors_wipe_qdrant_false(tmp_path, monkeypatch) -> None:
    """Query param `wipe_qdrant=false` should skip Qdrant entirely."""
    monkeypatch.setenv("INDEX_DATA_DIR", str(tmp_path))

    fake = _FakeQdrantClient(existing=["huggingface_models"])
    app = _build_test_app()
    with patch("qdrant_client.QdrantClient", return_value=fake), patch(
        "src.index.huggingface_models.config.load_config",
        return_value=_fake_config(),
    ):
        code, body = _request(
            app, "DELETE",
            "/v2/indices/huggingface_models/reset?wipe_qdrant=false",
        )

    assert code == HTTP_OK
    assert body["qdrant_skipped"] is True
    assert body["qdrant_collections_dropped"] == []
    assert fake.deleted == []


def test_reset_endpoint_honors_wipe_cache_true(tmp_path, monkeypatch) -> None:
    """Query param `wipe_cache=true` should delete the cache file."""
    monkeypatch.setenv("INDEX_DATA_DIR", str(tmp_path))

    cache = tmp_path / "providers.db"
    cache.write_bytes(b"fake")
    config = SimpleNamespace(
        qdrant=SimpleNamespace(url="http://x", api_key=None, prefer_grpc=False),
        paths=SimpleNamespace(cache_db_path=cache),
    )
    fake = _FakeQdrantClient(existing=[])
    app = _build_test_app()
    with patch("qdrant_client.QdrantClient", return_value=fake), patch(
        "src.index.huggingface_models.config.load_config",
        return_value=config,
    ):
        code, body = _request(
            app, "DELETE",
            "/v2/indices/huggingface_models/reset?wipe_cache=true",
        )

    assert code == HTTP_OK
    assert body["cache_cleared"] is True
    assert not cache.exists()


def test_reset_endpoint_returns_404_for_unknown_provider(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INDEX_DATA_DIR", str(tmp_path))
    app = _build_test_app()
    code, body = _request(app, "DELETE", "/v2/indices/not-a-real-provider/reset")
    assert code == HTTP_NOT_FOUND
    assert "unknown provider" in body["detail"].lower()


def test_reset_endpoint_is_idempotent(tmp_path, monkeypatch) -> None:
    """Calling reset twice on a provider with no DuckDB / no Qdrant
    collection still returns 200 with `duckdb_deleted=False`."""
    monkeypatch.setenv("INDEX_DATA_DIR", str(tmp_path))

    fake = _FakeQdrantClient(existing=[])
    app = _build_test_app()
    with patch("qdrant_client.QdrantClient", return_value=fake), patch(
        "src.index.huggingface_models.config.load_config",
        return_value=_fake_config(),
    ):
        code1, body1 = _request(app, "DELETE", "/v2/indices/huggingface_models/reset")
        code2, body2 = _request(app, "DELETE", "/v2/indices/huggingface_models/reset")

    assert code1 == HTTP_OK
    assert code2 == HTTP_OK
    assert body1["duckdb_deleted"] is False
    assert body2["duckdb_deleted"] is False


# ---------------------------------------------------------------------------
# DELETE /v2/indices/reset-all
# ---------------------------------------------------------------------------


def test_reset_all_endpoint_returns_one_result_per_provider(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INDEX_DATA_DIR", str(tmp_path))

    from src.v2.indices.reset import known_providers

    fake = _FakeQdrantClient(existing=[])
    app = _build_test_app()
    # Patch every config loader resolution so no real YAML is touched.
    with patch("qdrant_client.QdrantClient", return_value=fake), patch(
        "src.v2.indices.reset._import_config_loader",
        return_value=lambda: _fake_config(),
    ):
        code, body = _request(app, "DELETE", "/v2/indices/reset-all")

    assert code == HTTP_OK
    assert body["count"] == len(known_providers())
    returned_providers = {r["provider"] for r in body["results"]}
    assert returned_providers == set(known_providers())


# ---------------------------------------------------------------------------
# Full lifecycle smoke: ingest → reset → re-ingest
# ---------------------------------------------------------------------------


def test_full_lifecycle_ingest_reset_reingest(tmp_path, monkeypatch) -> None:
    """Cold-start the index, ingest a record, verify it lands, reset,
    re-ingest a different record, verify the fresh row replaces the
    old one (i.e. reset fully cleared the DuckDB)."""
    monkeypatch.setenv("INDEX_DATA_DIR", str(tmp_path))

    from src.index.huggingface_models.ingest.models import _record_from_info
    from src.index.huggingface_models.paths import get_huggingface_models_paths
    from src.index.huggingface_models.storage.duckdb_store import (
        HuggingFaceModelsStore,
    )

    paths = get_huggingface_models_paths()
    db_path = paths.duckdb_path

    # Round 1: ingest a record.
    info_1 = SimpleNamespace(downloads=100, tags=[], card_data={})
    store = HuggingFaceModelsStore.open(db_path)
    store.upsert_model(_record_from_info("org/m1", info_1))
    assert store.count("models") == 1
    store.close()

    # Reset via the HTTP endpoint.
    fake = _FakeQdrantClient(existing=["huggingface_models"])
    app = _build_test_app()
    with patch("qdrant_client.QdrantClient", return_value=fake), patch(
        "src.index.huggingface_models.config.load_config",
        return_value=_fake_config(),
    ):
        code, body = _request(app, "DELETE", "/v2/indices/huggingface_models/reset")
    assert code == HTTP_OK
    assert body["duckdb_deleted"] is True
    assert not db_path.exists()

    # Round 2: re-ingest a DIFFERENT record into a fresh DuckDB.
    info_2 = SimpleNamespace(downloads=200, tags=[], card_data={})
    store_2 = HuggingFaceModelsStore.open(db_path)
    store_2.upsert_model(_record_from_info("org/m2", info_2))
    rows = store_2.count("models")
    # v3.0.0: stored under the canonical URL id (projector canonicalises).
    row = store_2.fetch_model("https://huggingface.co/org/m2")
    # Critically, the old org/m1 row from round 1 is GONE — the reset
    # truly wiped the file, not just the rows.
    old_row = store_2.fetch_model("https://huggingface.co/org/m1")
    store_2.close()

    assert rows == 1
    assert row is not None and row["downloads"] == 200
    assert old_row is None
