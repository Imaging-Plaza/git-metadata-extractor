"""Run inside the extractor image on an internal Docker network."""

# Mounted standalone into a container, not imported as a test package.
# ruff: noqa: INP001, TRY003, EM102

import asyncio
import importlib.util
import os
import time
from http import HTTPStatus

import httpx

from git_metadata_extractor.providers.sources_service import (
    SourcesClient,
    current_sources_version,
    sources_scope,
)


def wait_for_health(url):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=2).status_code == HTTPStatus.OK:
                return
        except httpx.RequestError:
            pass
        time.sleep(0.25)
    raise RuntimeError(f"Test service did not become healthy: {url}")


async def check_sources():
    client = SourcesClient("http://sources:8080", "smoke-only")
    try:
        with sources_scope(client):
            result = await client.operation(
                "infoscience-acronym", {"acronym": "BOUNDARY"},
            )
            assert result.status == "ok", result
            assert result.data["records"][0]["name"] == "Boundary smoke organization"
            assert current_sources_version() == os.environ["EXPECTED_SOURCES_VERSION"]
        assert current_sources_version() is None
    finally:
        await client.aclose()


def main():
    for name in ("open_pulse_sources", "duckdb", "qdrant_client"):
        assert importlib.util.find_spec(name) is None, name
    for url in ("http://sources:8080/health", "http://extractor:1234/v2/health"):
        wait_for_health(url)
    asyncio.run(check_sources())
    response = httpx.post(
        "http://sources:8080/v2/extractor/infoscience-acronym",
        json={"acronym": "BOUNDARY"},
    )
    assert response.status_code == HTTPStatus.UNAUTHORIZED
    response = httpx.get(
        "http://extractor:1234/v2/extract/https://github.com/octocat/Hello-World",
        params={"agent_runtime": "rule_based", "include_run_report": "true"},
        headers={"Authorization": "Bearer smoke-only"},
        timeout=90,
    )
    response.raise_for_status()
    payload = response.json()
    assert payload["output"]["@graph"]
    assert payload["run_report"]
    print(
        "HTTP smoke passed: isolated imports, DuckDB lookup, auth, remote version, extraction and run report",
    )


if __name__ == "__main__":
    main()
