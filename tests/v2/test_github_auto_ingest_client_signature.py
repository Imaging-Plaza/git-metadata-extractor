"""Background indexing uses the remote service and reports submission failures."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from git_metadata_extractor.api import auto_ingest


@pytest.mark.parametrize(
    "kind,index,flag,helper,url",
    [
        (
            "repository",
            "github_repos",
            "V2_GITHUB_REPOS_RAG_AUTO_INGEST",
            "_maybe_schedule_github_repos_auto_ingest",
            "https://github.com/owner/repo",
        ),
        (
            "user",
            "github_users",
            "V2_GITHUB_USERS_RAG_AUTO_INGEST",
            "_maybe_schedule_github_users_auto_ingest",
            "https://github.com/owner",
        ),
        (
            "organization",
            "github_organizations",
            "V2_GITHUB_ORGS_RAG_AUTO_INGEST",
            "_maybe_schedule_github_orgs_auto_ingest",
            "https://github.com/orgs/owner",
        ),
    ],
)
def test_submission_routes_to_source_owned_index(
    monkeypatch, kind, index, flag, helper, url
):
    monkeypatch.setenv(flag, "true")
    client = SimpleNamespace(
        operation=AsyncMock(
            return_value=SimpleNamespace(
                status="ok", data={"job_id": "job", "status": "pending"}
            )
        )
    )
    classification = SimpleNamespace(
        detected_type=SimpleNamespace(value=kind), normalized_url=url
    )

    async def run():
        getattr(auto_ingest, helper)(
            classification=classification, run_id="run", sources_client=client
        )
        await asyncio.sleep(0)

    asyncio.run(run())
    client.operation.assert_awaited_once_with(
        "ensure-indexed",
        {
            "index": index,
            "target": "owner/repo" if kind == "repository" else "owner",
            "skip_existing": True,
            "embed": True,
        },
    )


@pytest.mark.parametrize("status", ["unauthorized", "unavailable", "invalid_response"])
def test_failed_submission_never_reports_ingested(monkeypatch, caplog, status):
    monkeypatch.setenv("V2_GITHUB_REPOS_RAG_AUTO_INGEST", "true")
    client = SimpleNamespace(
        operation=AsyncMock(return_value=SimpleNamespace(status=status, data={}))
    )
    classification = SimpleNamespace(
        detected_type=SimpleNamespace(value="repository"),
        normalized_url="https://github.com/owner/repo",
    )

    async def run():
        auto_ingest._maybe_schedule_github_repos_auto_ingest(
            classification=classification, run_id="run", sources_client=client
        )
        await asyncio.sleep(0)

    asyncio.run(run())
    assert status in caplog.text
    assert "submitted" not in caplog.text
