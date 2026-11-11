from __future__ import annotations

# Boundary stubs preserve production signatures; local imports keep each test's
# exercised integration explicit.
# ruff: noqa: ARG001, ARG002, ARG005, PLC0415, S106
import asyncio
import json

import pytest

from git_metadata_extractor.observation.query_log import QueryLog, query_log_var
from git_metadata_extractor.providers.detection.github_url_classifier import (
    classify_github_url,
)


@pytest.fixture
def trace():
    log = QueryLog(
        run_id="evidence-test", extract_full_path="https://github.com/acme/tool",
    )
    token = query_log_var.set(log)
    yield log
    query_log_var.reset(token)


def _nodes(document):
    return {node["@id"]: node for node in document["@graph"]}


def _person(name="Ada"):
    return {
        "id": "https://github.com/ada",
        "type": "schema:Person",
        "schema:name": name,
    }


def test_query_citation_requires_a_successful_retrieved_record(trace):
    from git_metadata_extractor.observation.evidence import activity, observe_provider

    class Orcid:
        def search_persons(self, query):
            return [{"id": "https://orcid.org/0000-0001-0002-0003", "name": "Ada"}]

    provider = observe_provider(Orcid(), "orcid")
    with activity("person_agent", kind="llm", entity_id=_person()["id"]):
        provider.search_persons("Ada")
        evidence = trace.evidence.available_sources()
        assert len(evidence) == 1
        query_id = evidence[0]["query_id"]
        assert (
            trace.evidence.cite(
                property="schema:name", value="Invented", query_id=query_id,
            )["accepted"]
            is False
        )
        assert (
            trace.evidence.cite(property="schema:name", value="Ada", query_id=query_id)[
                "accepted"
            ]
            is True
        )
        trace.evidence.observe(_person())

    nodes = _nodes(trace.evidence.document([_person()]))
    assertion = next(
        n for n in nodes.values() if n.get("rdf:predicate") == {"@id": "schema:name"}
    )
    assert assertion["gme:evidenceKind"] == "retrieved"
    assert assertion["prov:wasDerivedFrom"] == [{"@id": query_id}]
    query = nodes[query_id]
    assert query["gme:status"] == "success"
    assert query["gme:service"] == "orcid.search_persons"
    assert query["gme:source"] == [{"@id": "https://orcid.org/0000-0001-0002-0003"}]


def test_consulting_a_query_does_not_claim_it_produced_the_value(trace):
    from git_metadata_extractor.observation.evidence import activity, observe_provider

    class Search:
        def search(self, query):
            return [{"url": "https://example.org/person", "name": "Ada"}]

    with activity("person_agent", kind="llm", entity_id=_person()["id"]):
        observe_provider(Search(), "search").search("Ada")
        trace.evidence.observe(_person())
    assertion = next(
        n
        for n in trace.evidence.document([_person()])["@graph"]
        if "rdf:predicate" in n
    )
    assert assertion["gme:evidenceKind"] == "llm"
    assert "prov:wasDerivedFrom" not in assertion
    assert assertion["gme:matchingQuery"]


def test_parallel_agents_cannot_cite_each_others_sources(trace):
    from git_metadata_extractor.observation.evidence import activity, observe_provider

    class Search:
        async def search(self, query):
            await asyncio.sleep(0)
            return [{"url": f"https://example.org/{query}", "name": query}]

    async def run(name):
        with activity(
            "person_agent", kind="llm", entity_id=f"https://github.com/{name}",
        ):
            await observe_provider(Search(), "search").search(name)
            await asyncio.sleep(0)
            sources = trace.evidence.available_sources()
            assert len(sources) == 1
            assert sources[0]["sources"] == [f"https://example.org/{name}"]
            trace.evidence.observe(
                {"id": f"https://github.com/{name}", "schema:name": name},
            )

    async def together():
        await asyncio.gather(run("ada"), run("bob"))

    asyncio.run(together())


def test_failed_query_and_secret_arguments_are_not_exported_as_evidence(trace):
    from git_metadata_extractor.observation.evidence import activity, observe_provider

    class Broken:
        def search(self, query, token=None):
            message = "private-token"
            raise ValueError(message)

    with activity("person_agent", kind="llm"):
        with pytest.raises(ValueError, match="private-token"):
            observe_provider(Broken(), "search").search(
                "https://user:password@example.org/?token=private-token&q=Ada",
                token="private-token",
            )
        assert trace.evidence.available_sources() == []
    document = trace.evidence.document([])
    encoded = json.dumps(document)
    assert "private-token" not in encoded
    assert "password" not in encoded
    assert any(n.get("gme:status") == "failed" for n in document["@graph"])


def test_export_only_contains_final_public_values(trace):
    from git_metadata_extractor.observation.evidence import activity

    with activity("person_agent", kind="rule_based"):
        trace.evidence.observe(
            {**_person(), "schema:email": "secret@example.org", "_bio": "private"},
        )
    document = trace.evidence.document([_person()])
    assert "secret@example.org" not in json.dumps(document)
    assert "_bio" not in json.dumps(document)


def test_pipeline_preserves_original_agent_for_unchanged_fields(trace):
    from git_metadata_extractor.observation.evidence import activity
    from git_metadata_extractor.pipeline.runner import Stage, run_pipeline
    from git_metadata_extractor.pipeline.state import PipelineState

    with activity("person_agent", kind="rule_based"):
        trace.evidence.observe(_person())
    state = PipelineState(
        run_id="evidence-test",
        classification=classify_github_url("https://github.com/acme/widget", probe_accounts=False),
        runtime="hybrid",
        providers=None,
        payload={"@graph": [_person()]},
    )

    def change(state):
        state.payload["@graph"][0]["schema:description"] = "Researcher"

    asyncio.run(run_pipeline(state, [Stage(name="enrich", run=change)]))
    nodes = _nodes(trace.evidence.document(state.payload["@graph"]))
    assertions = {
        n["rdf:predicate"]["@id"]: n for n in nodes.values() if "rdf:predicate" in n
    }
    name_activity = nodes[assertions["schema:name"]["prov:wasGeneratedBy"]["@id"]]
    description_activity = nodes[
        assertions["schema:description"]["prov:wasGeneratedBy"]["@id"]
    ]
    assert name_activity["schema:name"] == "person_agent"
    assert description_activity["schema:name"] == "enrich"


def test_retry_records_agent_and_provider_result(trace):
    from git_metadata_extractor.agents.models import AgentResult, ProviderSet
    from git_metadata_extractor.agents.retry import with_retry

    class GitHub:
        def get_user(self, login):
            return {"html_url": "https://github.com/ada", "name": "Ada"}

    async def person_agent(context, providers):
        user = providers.github.get_user("ada")
        return AgentResult(data=_person(user["name"]))

    result = asyncio.run(
        with_retry(
            person_agent,
            providers=ProviderSet(github=GitHub()),
            context={"agent_runtime": "rule_based"},
        ),
    )
    nodes = list(_nodes(trace.evidence.document([result.data])).values())
    assert any(
        n.get("gme:service") == "github.get_user" and n.get("gme:status") == "success"
        for n in nodes
    )
    assert any(n.get("schema:name", "").endswith("person_agent") for n in nodes)


def test_cached_verdict_retains_original_model_query_and_run(trace, tmp_path):
    from git_metadata_extractor.agents.llm._verdict_cache import (
        get_cached_agent_verdict,
        store_agent_verdict,
    )
    from git_metadata_extractor.agents.models import AgentResult
    from git_metadata_extractor.observation.evidence import activity, observe_provider
    from git_metadata_extractor.providers.cache import ProviderCache

    cache = ProviderCache(tmp_path / "cache.db")

    class Search:
        def search(self, query):
            return [{"url": "https://example.org/ada", "name": "Ada"}]

    with activity("person_agent", kind="llm"):
        with activity(
            "model_call", kind="llm", model="test-model", provider="test-provider",
        ):
            observe_provider(Search(), "orcid").search("Ada")
            trace.evidence.observe(_person())
        store_agent_verdict(
            cache,
            agent_name="person",
            identity={"login": "ada"},
            result=AgentResult(
                data=_person(), model="test-model", provider="test-provider",
            ),
        )
    new_log = QueryLog("cached-run", "https://github.com/acme/tool")
    token = query_log_var.set(new_log)
    try:
        with activity("person_agent", kind="llm"):
            result = get_cached_agent_verdict(
                cache, agent_name="person", identity={"login": "ada"},
            )
            new_log.evidence.observe(result.data)
        nodes = list(_nodes(new_log.evidence.document([result.data])).values())
        assert any(n.get("gme:model") == "test-model" for n in nodes)
        assert any(n.get("gme:cache_hit") is True and n.get("prov:used") for n in nodes)
        queries = [n for n in nodes if "gme:service" in n]
        assert len(queries) == 1
        assert queries[0]["@id"].startswith("urn:pulse:run:evidence-test:")
    finally:
        query_log_var.reset(token)


def test_model_runtime_records_model_and_supplies_checked_citation_tools(
    trace, monkeypatch,
):
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    from git_metadata_extractor.agents.llm import runtime as module
    from git_metadata_extractor.observation.evidence import activity

    seen = []

    def model(messages, info):
        seen.extend(t.name for t in info.function_tools)
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name,
                    {
                        "response": {
                            "id": "https://github.com/ada",
                            "schema:name": "Ada",
                        },
                    },
                ),
            ],
        )

    runtime = module.V2LLMRuntime()
    monkeypatch.setattr(
        runtime,
        "_resolve_model_config",
        lambda: {"model": "fixture-model", "provider": "fixture"},
    )
    monkeypatch.setattr(
        module, "create_pydantic_ai_model", lambda config: FunctionModel(model),
    )
    with activity("person_agent", kind="llm"):
        result = asyncio.run(
            runtime.run_json_prompt(system_prompt="Extract", user_prompt="Ada"),
        )
    assert {"list_source_evidence", "cite_source"} <= set(seen)
    nodes = list(_nodes(trace.evidence.document([result.payload])).values())
    assert any(
        n.get("gme:model") == "fixture-model" and n.get("gme:provider") == "fixture"
        for n in nodes
    )


def test_evidence_document_round_trips_as_rdf(trace):
    from rdflib import Graph, URIRef

    from git_metadata_extractor.observation.evidence import activity

    with activity("person_agent", kind="rule_based"):
        trace.evidence.observe(_person())
    graph = Graph().parse(
        data=json.dumps(trace.evidence.document([_person()])), format="json-ld",
    )
    assert list(
        graph.subjects(
            URIRef("http://www.w3.org/1999/02/22-rdf-syntax-ns#subject"),
            URIRef("https://github.com/ada"),
        ),
    )


def test_substrate_persists_the_same_evidence_returned_to_the_caller(
    trace, monkeypatch,
):
    from rdflib import Dataset, URIRef

    from git_metadata_extractor.observation.evidence import activity
    from git_metadata_extractor.pipeline.run import (
        _record_extraction_run,
        _write_substrate,
    )
    from git_metadata_extractor.pipeline.state import PipelineState
    from git_metadata_extractor.store import oxigraph

    class Classification:
        normalized_url = "https://github.com/acme/tool"

    captured = []

    class Store:
        base_url = "http://test-store"

        async def load_nquads(self, data):
            captured.append(data)
            return len(data)

    monkeypatch.setattr(oxigraph, "store_from_config", lambda *a, **kw: Store())
    monkeypatch.setenv("V2_SUBSTRATE_VALIDATE", "false")
    with activity("person_agent", kind="rule_based"):
        trace.evidence.observe(_person())
    state = PipelineState(
        run_id="evidence-test",
        classification=classify_github_url("https://github.com/acme/widget", probe_accounts=False),
        runtime="rule_based",
        providers=None,
        payload={"@graph": [_person()]},
        substrate={"@context": {}, "@graph": []},
    )
    _record_extraction_run(state)
    asyncio.run(_write_substrate(state))
    evidence_graph = next(
        g for g in state.substrate["@graph"] if g["@id"].endswith("#evidence")
    )
    assert evidence_graph["@graph"] == state.artifacts.provenance["@graph"]
    dataset = Dataset()
    dataset.parse(data=captured[0], format="nquads")
    assert len(dataset.graph(URIRef("urn:pulse:run:evidence-test#evidence"))) > 0


def test_error_result_is_not_a_successful_source(trace):
    from git_metadata_extractor.observation.evidence import activity, observe_provider

    class Search:
        def search(self):
            return {"error": "Not available", "name": "Ada"}

    with activity("person_agent", kind="llm"):
        observe_provider(Search(), "search").search()
        assert trace.evidence.available_sources() == []


@pytest.mark.parametrize("tool_form", ["tool", "async_callable"])
def test_real_model_tool_cycle_can_cite_a_retrieved_value(trace, monkeypatch, tool_form):
    from pydantic_ai import Tool
    from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
    from pydantic_ai.models.function import FunctionModel

    from git_metadata_extractor.agents.llm import runtime as module
    from git_metadata_extractor.observation.evidence import activity

    def fetch_person() -> dict[str, str]:
        return {"url": "https://example.org/ada", "name": "Ada"}

    async def async_fetch_person() -> dict[str, str]:
        await asyncio.sleep(0)
        return fetch_person()

    async_fetch_person.__name__ = "fetch_person"
    tool = Tool(fetch_person) if tool_form == "tool" else async_fetch_person

    def model(messages, info):
        returns = [
            p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)
        ]
        if not returns:
            return ModelResponse(parts=[ToolCallPart("fetch_person", {})])
        last = returns[-1]
        if last.tool_name == "fetch_person":
            return ModelResponse(parts=[ToolCallPart("list_source_evidence", {})])
        if last.tool_name == "list_source_evidence":
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "cite_source",
                        {
                            "property": "schema:name",
                            "value": "Ada",
                            "query_id": last.content[0]["query_id"],
                            "entity_id": "https://github.com/ada",
                        },
                    ),
                ],
            )
        assert last.content["accepted"] is True
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, {"response": _person()})],
        )

    runtime = module.V2LLMRuntime()
    monkeypatch.setattr(
        runtime,
        "_resolve_model_config",
        lambda: {"model": "fixture-model", "provider": "fixture"},
    )
    monkeypatch.setattr(
        module, "create_pydantic_ai_model", lambda config: FunctionModel(model),
    )
    with activity("person_agent", kind="llm"):
        result = asyncio.run(
            runtime.run_json_prompt(
                system_prompt="Extract", user_prompt="Ada", tools=[tool],
            ),
        )
    document = trace.evidence.document([result.payload])
    assertion = next(n for n in document["@graph"] if "rdf:predicate" in n)
    assert assertion["gme:evidenceKind"] == "retrieved"
    assert assertion["prov:wasDerivedFrom"]


def test_rejected_attempt_cannot_own_the_successful_retry_output(trace):
    from git_metadata_extractor.agents.models import AgentResult
    from git_metadata_extractor.agents.retry import with_retry

    attempts = []

    def agent(context, providers):
        attempts.append(1)
        return AgentResult(
            data=_person(),
            is_partial=len(attempts) == 1,
            failure_reason="incomplete" if len(attempts) == 1 else None,
        )

    result = asyncio.run(with_retry(agent, max_retries=1, backoff_base=0))
    nodes = _nodes(trace.evidence.document([result.data]))
    invocations = [n for n in nodes.values() if n.get("gme:kind") == "rule_based"]
    assert [n["gme:status"] for n in invocations] == ["rejected", "success"]
    assertion = next(n for n in nodes.values() if "rdf:predicate" in n)
    assert assertion["prov:wasGeneratedBy"] == {"@id": invocations[1]["@id"]}


def test_restored_context_queries_are_history_not_new_agent_inputs(trace):
    from git_metadata_extractor.observation.evidence import activity, observe_provider

    class Search:
        def search(self):
            return {"url": "https://example.org/old-repository", "name": "Ada"}

    with activity("context_gather", kind="retrieval"):
        observe_provider(Search(), "github").search()
    with activity("person_agent", kind="llm"):
        trace.evidence.observe(_person())
        packet = trace.evidence.snapshot()
    new_log = QueryLog("other-run", "https://github.com/other/repository")
    token = query_log_var.set(new_log)
    try:
        new_log.evidence.restore(packet)
        with activity("person_agent", kind="llm", entity_id="https://github.com/bob"):
            assert new_log.evidence.available_sources() == []
    finally:
        query_log_var.reset(token)


def test_url_encoding_does_not_bypass_query_privacy(trace):
    from git_metadata_extractor.observation.evidence import activity, observe_provider

    class Search:
        def search(self, url):
            return {"url": "https://example.org/alice@example.org?api_key=secret-value"}

    with activity("person_agent", kind="rule_based"):
        observe_provider(Search(), "search").search(
            "https://example.org/?q=alice%40example.org",
        )
    encoded = json.dumps(trace.evidence.document([]))
    assert "alice" not in encoded
    assert "secret-value" not in encoded


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        (
            {
                "orcid_id": "0000-0002-1825-0097",
                "full_name": "Ada",
                "given_names": "Ada",
                "family_name": None,
                "biography": None,
                "employments": [],
                "educations": [],
                "external_ids": [],
            },
            "https://orcid.org/0000-0002-1825-0097",
        ),
        (
            {"ror_id": "02s376052", "name": "EPFL", "country": "CH", "score": 0.9},
            "https://ror.org/02s376052",
        ),
        (
            {
                "infoscience_url": "https://infoscience.epfl.ch/entities/publication/example",
                "title": "Paper",
            },
            "https://infoscience.epfl.ch/entities/publication/example",
        ),
    ],
)
def test_provider_record_identifiers_are_exported_as_source_urls(
    trace, record, expected,
):
    from git_metadata_extractor.observation.evidence import activity, observe_provider

    class Provider:
        def search(self):
            return [record]

    with activity("person_agent", kind="llm"):
        observe_provider(Provider(), "fixture").search()
        assert trace.evidence.available_sources()[0]["sources"] == [expected]


def test_successful_current_scout_queries_are_available_to_downstream_agents(trace):
    from git_metadata_extractor.observation.evidence import activity, observe_provider

    class Search:
        def search(self):
            return {"orcid_id": "0000-0002-1825-0097", "name": "Ada"}

    with activity("LLMContextSummaryAgentV2.run", kind="llm"):
        observe_provider(Search(), "orcid").search()
    with activity("person_agent", kind="llm", entity_id=_person()["id"]):
        sources = trace.evidence.available_sources()
        assert len(sources) == 1
        assert trace.evidence.cite(
            property="schema:name", value="Ada", query_id=sources[0]["query_id"],
        )["accepted"]


def test_malformed_url_in_search_text_does_not_break_provider(trace):
    from git_metadata_extractor.observation.evidence import activity, observe_provider

    class Search:
        def search(self, query):
            return {"url": "https://[placeholder]", "name": "Ada"}

    with activity("person_agent", kind="rule_based"):
        result = observe_provider(Search(), "search").search(
            "Docs mentioning https://[placeholder]",
        )
        assert result["name"] == "Ada"
