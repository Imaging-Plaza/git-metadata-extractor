# ruff: noqa: PLC0415 - integration tests name collaborators at their use sites
"""LLM tool interfaces preserve their results across the HTTP boundary."""

import asyncio
import importlib
import json

import httpx
import pytest

from git_metadata_extractor.providers.sources_service import SourcesClient

TOP_K = 2

PROVIDERS = [
    ("epfl_graph", "EpflGraphRagProvider"),
    ("github", "GitHubRagProvider"),
    ("swissubase", "SwissubaseRagProvider"),
    ("zenodo", "ZenodoRagProvider"),
    ("ror", "RorRagProvider"),
    ("snsf", "SnsfRagProvider"),
    ("openalex", "OpenAlexRagProvider"),
    ("huggingface", "HuggingFaceRagProvider"),
    ("orcid", "OrcidRagProvider"),
    ("oamonitor", "OamonitorRagProvider"),
    ("renkulab", "RenkulabRagProvider"),
    ("infoscience", "InfoscienceRagProvider"),
    ("ethz_research_collection", "EthzResearchCollectionRagProvider"),
    ("federated", "FederatedRagProvider"),
]


@pytest.mark.parametrize(("name", "class_name"), PROVIDERS)
def test_search_tools_preserve_name_arguments_and_hits(name, class_name):
    module = importlib.import_module(
        f"git_metadata_extractor.agents.llm.agent_tools.{name}_rag",
    )
    factory = getattr(module, f"make_{name}_rag_search_tool")
    provider_type = getattr(
        importlib.import_module(f"git_metadata_extractor.providers.{name}_rag"),
        class_name,
    )
    hits = [
        {"id": "record-1", "score": 0.7, "snippet": "Evidence", "collection": "example"},
    ]
    expected = (
        {"hits": hits, "by_index": {}, "errors": {}} if name == "federated" else hits
    )

    def handle(request):
        assert request.url.path == f"/v2/retrieval/{name}/search"
        payload = json.loads(request.content)
        assert payload["query"] == "Example"
        assert payload["top_k"] == TOP_K
        return httpx.Response(200, json={"data": expected})

    async def run():
        client = SourcesClient(
            "http://sources", "test", transport=httpx.MockTransport(handle),
        )
        try:
            tool = factory(provider_type(client=client))
            assert tool.name == (
                "search_epfl_graph_disciplines"
                if name == "epfl_graph"
                else f"search_{name}_rag"
            )
            assert await tool.function(query="Example", top_k=2) == expected
        finally:
            await client.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("name", "class_name"),
    [
        ("infoscience", "InfoscienceRagProvider"),
        ("ethz_research_collection", "EthzResearchCollectionRagProvider"),
    ],
)
def test_article_tools_preserve_hydration_and_reject_chunks_as_records(
    name, class_name,
):
    module = importlib.import_module(
        f"git_metadata_extractor.agents.llm.agent_tools.{name}_rag",
    )
    provider_type = getattr(
        importlib.import_module(f"git_metadata_extractor.providers.{name}_rag"),
        class_name,
    )

    async def run():
        calls = []

        def handle(request):
            calls.append((request.url.path, json.loads(request.content)))
            return httpx.Response(
                200, json={"data": [{"id": "one", "text": "full text"}]},
            )

        client = SourcesClient(
            "http://sources", "test", transport=httpx.MockTransport(handle),
        )
        try:
            provider = provider_type(client=client)
            chunks = getattr(module, f"make_{name}_rag_fetch_chunks_tool")(provider)
            records = getattr(module, f"make_{name}_rag_fetch_records_tool")(provider)
            assert await chunks.function(article_uuid="article", max_chunks=3) == [
                {"id": "one", "text": "full text"},
            ]
            assert await records.function(collection="articles", ids=["one"]) == [
                {"id": "one", "text": "full text"},
            ]
            assert await records.function(collection="chunks", ids=["one"]) == []
            assert calls == [
                (
                    f"/v2/retrieval/{name}/fetch_chunks",
                    {"article_uuid": "article", "max_chunks": 3},
                ),
                (
                    f"/v2/retrieval/{name}/fetch_records",
                    {"collection": "articles", "ids": ["one"]},
                ),
            ]
        finally:
            await client.aclose()

    asyncio.run(run())


def test_snsf_tool_uses_async_faceted_search():
    from git_metadata_extractor.agents.llm.agent_tools.snsf_grants import (
        make_search_snsf_grants_tool,
    )
    from git_metadata_extractor.providers.snsf_grants import SnsfGrantsProvider

    async def run():
        def handle(request):
            assert request.url.path == "/v2/retrieval/snsf_grants/search"
            assert json.loads(request.content)["filters"] == {"state": ["Completed"]}
            return httpx.Response(200, json={"data": [{"grant_number": "123"}]})

        client = SourcesClient(
            "http://sources", "test", transport=httpx.MockTransport(handle),
        )
        try:
            tool = make_search_snsf_grants_tool(SnsfGrantsProvider(client=client))
            assert tool.name == "search_snsf_grants"
            assert await tool.function(state=["Completed"]) == [{"grant_number": "123"}]
        finally:
            await client.aclose()

    asyncio.run(run())
