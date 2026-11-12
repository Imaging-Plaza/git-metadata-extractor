"""Stage-side source operations use HTTP clients without local index access."""

from __future__ import annotations

import asyncio
from functools import wraps
from types import SimpleNamespace


def run_async(fn):
    @wraps(fn)
    def run(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))

    return run


class Client:
    def __init__(self, data=None, status="ok"):
        self.calls = []
        self.data = data or {}
        self.status = status

    async def operation(self, name, payload):
        self.calls.append((name, payload))
        return SimpleNamespace(status=self.status, data=self.data, error="unavailable")


@run_async
async def test_ancestor_lookup_preserves_chain():
    from git_metadata_extractor.pipeline.stages.rule_based_disciplines import (
        _fetch_category_chain_qids,
    )

    client = Client(
        {
            "categories": [
                {"category_id": "leaf", "wikidata_qid": "Q1", "parent_id": "parent"},
                {"category_id": "parent", "wikidata_qid": "Q2", "parent_id": None},
            ]
        }
    )
    result = await _fetch_category_chain_qids(["leaf"], client)
    assert result == {"leaf": ("Q1", "parent"), "parent": ("Q2", None)}
    assert client.calls == [
        ("category-ancestors", {"category_ids": ["leaf"], "max_depth": 8})
    ]


@run_async
async def test_exact_evidence_uses_sources_client():
    from git_metadata_extractor.pipeline.stages.refine_with_llm import (
        _gather_federated_evidence,
    )

    client = Client({"records": [{"name": "Lab", "acronym": "UPLAB"}]})
    result = await _gather_federated_evidence(
        "UPLAB", providers=SimpleNamespace(sources=client)
    )
    assert result["infoscience_duckdb_hits"] == [{"name": "Lab", "acronym": "UPLAB"}]
    assert {name for name, _ in client.calls} == {
        "infoscience-acronym",
        "zenodo-community",
    }


@run_async
async def test_concept_metadata_from_remote_annotation(monkeypatch):
    from git_metadata_extractor.pipeline.stages import concept_tagging as stage

    client = Client(
        {
            "keywords": ["science"],
            "concepts": [{"label": "Science"}],
            "disciplines": [],
            "warnings": [],
        }
    )
    monkeypatch.setattr(stage, "current_sources_client", lambda: client, raising=False)
    root, result = await stage.run_concept_tagging_stage(
        root_entity={}, readme_text="A scientific README"
    )
    assert root["_keywords"] == ["science"]
    assert result.concepts == [{"label": "Science"}]
    assert client.calls[0][0] == "epfl-concepts"


@run_async
async def test_auto_ingest_submits_remote_job(monkeypatch, caplog):
    from git_metadata_extractor.api import auto_ingest

    client = Client({"job_id": "remote-job", "status": "pending"})
    monkeypatch.setenv("V2_GITHUB_REPOS_RAG_AUTO_INGEST", "true")
    classification = SimpleNamespace(
        detected_type=SimpleNamespace(value="repository"),
        normalized_url="https://github.com/owner/repo",
    )
    with caplog.at_level("INFO"):
        auto_ingest._maybe_schedule_github_repos_auto_ingest(
            classification=classification, run_id="run", sources_client=client
        )
        await asyncio.sleep(0)
    assert client.calls == [
        (
            "ensure-indexed",
            {
                "index": "github_repos",
                "target": "owner/repo",
                "skip_existing": True,
                "embed": True,
            },
        )
    ]
    assert "submitted" in caplog.text
    assert "chunks_embedded" not in caplog.text


@run_async
async def test_taxonomy_selection_keeps_allowed_ancestors_and_existing_values(monkeypatch):
    from git_metadata_extractor.pipeline.stages import rule_based_disciplines as stage
    from git_metadata_extractor.providers import epfl_graph_rag
    from git_metadata_extractor.pipeline.stages.models import AssembledOutput

    client = Client({"categories": [
        {"category_id": "leaf", "wikidata_qid": "Q1254373", "parent_id": "parent"},
        {"category_id": "parent", "wikidata_qid": "Q7112556", "parent_id": None},
    ]})
    class Search:
        async def search(self, query, **kwargs):
            return [{"category_id": "leaf", "score": 0.8}]

    monkeypatch.setattr(stage, "current_sources_client", lambda: client)
    monkeypatch.setattr(epfl_graph_rag, "build_default_provider", lambda **kwargs: Search())
    assembled = AssembledOutput(root_entity={"@type": "schema:SoftwareSourceCode", "schema:name": "repo"})
    tagged, warnings = await stage.tag_disciplines(assembled, readme_text="Research")
    assert tagged.root_entity["pulse:discipline"] == ["wd:Q1254373", "wd:Q7112556"]
    assert "walk=['leaf', 'parent']" in warnings[0]
    assert "pulse:discipline" not in assembled.root_entity
    client.calls.clear()
    repeated, _ = await stage.tag_disciplines(tagged, readme_text="Other signal")
    assert repeated is tagged
    assert client.calls == []


@run_async
async def test_unconfigured_sources_leave_annotation_untouched(monkeypatch):
    from git_metadata_extractor.pipeline.stages import concept_tagging as stage
    monkeypatch.setattr(stage, "current_sources_client", lambda: None)
    root = {"schema:name": "repo"}
    tagged, result = await stage.run_concept_tagging_stage(root_entity=root, readme_text="Research")
    assert tagged == {"schema:name": "repo"}
    assert result.warnings == ["concept_tagging: sources service unavailable"]


@run_async
async def test_concept_service_failure_is_warning(monkeypatch):
    from git_metadata_extractor.pipeline.stages import concept_tagging as stage
    monkeypatch.setattr(stage, "current_sources_client", lambda: Client(status="unavailable"))
    tagged, result = await stage.run_concept_tagging_stage(root_entity={}, readme_text="Research")
    assert tagged == {}
    assert result.warnings == ["concept_tagging: sources annotation unavailable"]


@run_async
async def test_annotation_uses_published_http_operation(monkeypatch):
    import httpx
    from git_metadata_extractor.pipeline.stages import concept_tagging as stage
    from git_metadata_extractor.providers.sources_service import SourcesClient

    observed = []
    def respond(request):
        observed.append(request.url.path)
        return httpx.Response(200, json={
            "data": {"keywords": ["scientific"], "concepts": [], "disciplines": [], "warnings": []},
            "service_version": "test-build",
        })
    client = SourcesClient("https://sources.test", "test-token", transport=httpx.MockTransport(respond))
    monkeypatch.setattr(stage, "current_sources_client", lambda: client)
    try:
        root, result = await stage.run_concept_tagging_stage(root_entity={}, readme_text="A README")
    finally:
        await client.aclose()
    assert observed == ["/v2/extractor/epfl-concepts"]
    assert root == {"_keywords": ["scientific"]}
    assert result.warnings == []
