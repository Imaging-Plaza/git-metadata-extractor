"""Tests for the graph query API — `PROVENANCE_ARCHITECTURE.md` phase 7.

Three endpoints over the accumulated store: what it holds, one canonical
entity, and why each chosen value won.

**The security-relevant assertion is the absence of one.** No endpoint accepts
a SPARQL string. Oxigraph ships no authentication and its `/store` endpoint is
writable by anyone who can reach it, so proxying a caller's query would be an
unauthenticated write primitive one `INSERT` away — the compose service is not
port-published for the same reason.
`test_no_endpoint_accepts_a_sparql_string` is what keeps that true as endpoints
are added.

Driven against `httpx.MockTransport` at the store boundary rather than a live
Oxigraph: what is under test here is the request handling, the query
construction and the response shape. That the queries mean what they claim
against a real RDF-star store is verified separately (§3k, §3n of the handoff).
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, get_args

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from pydantic import BaseModel

from git_metadata_extractor.api import v2_router
from git_metadata_extractor.app import app as main_app
from git_metadata_extractor.unify.provenance import PROV_GRAPH
from git_metadata_extractor.unify.runner import CANONICAL_GRAPH

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi.dependencies.models import Dependant

HTTP_OK = 200
HTTP_BAD_REQUEST = 400
HTTP_NOT_FOUND = 404
HTTP_UNAUTHORIZED = 401
HTTP_UNAVAILABLE = 503

ORCID_IRI = "https://orcid.org/0000-0002-1825-0097"
PROFILE = "urn:pulse:profile:github:jane"
TOKEN = "test-token"  # noqa: S105 — the value only has to be non-empty


def _results(bindings: list[dict[str, Any]]) -> str:
    return json.dumps({"head": {"vars": []}, "results": {"bindings": bindings}})


def _uri(value: str) -> dict[str, str]:
    return {"type": "uri", "value": value}


def _lit(value: str, datatype: str | None = None) -> dict[str, str]:
    out = {"type": "literal", "value": value}
    if datatype:
        out["datatype"] = datatype
    return out


class _FakeStore:
    """Answers each SPARQL query by matching on a fragment of its text.

    Keyed on fragments rather than exact strings so a comment or a reformat in
    the query does not break the test — but on fragments specific enough that
    the *wrong* query cannot accidentally match, which is the point of
    recording `self.queries` for the injection test to inspect.
    """

    def __init__(self, answers: dict[str, list[dict[str, Any]]]) -> None:
        self._answers = answers
        self.queries: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        if request.url.path == "/query":
            query = body.removeprefix("query=")
            self.queries.append(httpx.QueryParams(body).get("query") or query)
            for fragment, bindings in self._answers.items():
                if fragment in self.queries[-1]:
                    return httpx.Response(200, text=_results(bindings))
            return httpx.Response(200, text=_results([]))
        return httpx.Response(204)


@pytest.fixture
def app() -> FastAPI:
    test_app = FastAPI()
    test_app.include_router(v2_router)
    return test_app


def _get(app: FastAPI, path: str, *, token: str | None = TOKEN, **params: str) -> tuple[int, Any]:
    async def _run() -> tuple[int, Any]:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await client.get(path, params=params, headers=headers)
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, response.text

    return asyncio.run(_run())


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Install a fake store and return a setter for its answers."""
    from git_metadata_extractor.store import oxigraph

    monkeypatch.setenv("API_TOKEN", TOKEN)
    monkeypatch.setenv("V2_SUBSTRATE_STORE_URL", "http://oxigraph:7878")
    holder: dict[str, _FakeStore] = {}

    def install(answers: dict[str, list[dict[str, Any]]]) -> _FakeStore:
        fake = _FakeStore(answers)
        real = oxigraph.store_from_config
        monkeypatch.setattr(
            oxigraph,
            "store_from_config",
            lambda url, **kwargs: real(
                url,
                **{**kwargs, "transport": httpx.MockTransport(fake.handler)},
            ),
        )
        holder["store"] = fake
        return fake

    return install


# --------------------------------------------------------------------------
# the assertion that is an absence
# --------------------------------------------------------------------------


# What the SPARQL 1.1 Protocol carries a query or an update under, plus the
# obvious shorthands.
SPARQL_PARAMETER_NAMES = {"query", "sparql", "q", "where", "update"}


def _model_field_names(annotation: Any, seen: set[type[BaseModel]]) -> Iterator[str]:
    """Every field name and alias of the Pydantic models inside `annotation`.

    Through `Optional`, unions and containers, so a body of
    `{"items": [{"sparql": ...}]}` is seen as well as a flat one.
    """
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if annotation in seen:
            return
        seen.add(annotation)
        for name, field in annotation.model_fields.items():
            yield name
            yield from (
                alias
                for alias in (field.alias, field.validation_alias)
                if isinstance(alias, str)
            )
            yield from _model_field_names(field.annotation, seen)
        return
    for arg in get_args(annotation):
        yield from _model_field_names(arg, seen)


def _accepted_names(dependant: Dependant) -> Iterator[str]:
    """Every name a request can hand this endpoint a value under.

    The wire alias as well as the Python name — `prop` arrives as
    `?property=` — for path, query, header, cookie and body parameters, the
    fields of any Pydantic model among them, and all of that again for each
    sub-dependency, since a `Depends` declares parameters of its own.
    """
    for param in (
        *dependant.path_params,
        *dependant.query_params,
        *dependant.header_params,
        *dependant.cookie_params,
        *dependant.body_params,
    ):
        yield param.name
        yield param.alias
        yield from _model_field_names(param.field_info.annotation, set())
    for sub_dependant in dependant.dependencies:
        yield from _accepted_names(sub_dependant)


def test_no_endpoint_accepts_a_sparql_string() -> None:
    """Oxigraph has no auth and `/store` is writable by anyone who reaches it.

    So an endpoint that passed a caller's query through would be an
    unauthenticated write primitive one `INSERT` away. Every query is fixed
    text with IRIs substituted through `iri_term`, which *refuses* anything
    RDF forbids rather than escaping it.

    Asserted over the served app's whole route table rather than per endpoint,
    so a new endpoint taking a `query` — as a query parameter, a body field or
    through a dependency, under any path — fails here without anyone
    remembering to add a test. A route FastAPI cannot describe (a raw
    Starlette route, a mounted sub-app) declares nothing to inspect, so it
    fails too unless it is one of FastAPI's own schema and docs pages. A
    handler reading the raw `Request` is beyond any route table.
    """
    framework_pages = {
        main_app.openapi_url,
        main_app.docs_url,
        main_app.redoc_url,
        main_app.swagger_ui_oauth2_redirect_url,
    } - {None}
    uninspectable = [
        getattr(route, "path", repr(route))
        for route in main_app.routes
        if not isinstance(route, APIRoute)
        and getattr(route, "path", None) not in framework_pages
    ]
    assert not uninspectable, f"routes with no parameters to inspect: {uninspectable}"

    endpoints = [route for route in main_app.routes if isinstance(route, APIRoute)]
    assert any(route.path.startswith("/v2/graph") for route in endpoints), (
        "no graph routes registered"
    )
    offenders = {
        f"{','.join(sorted(route.methods))} {route.path}": sorted(hits)
        for route in endpoints
        if (
            hits := {name.lower() for name in _accepted_names(route.dependant)}
            & SPARQL_PARAMETER_NAMES
        )
    }
    assert not offenders, f"endpoints accepting a SPARQL string: {offenders}"


def test_a_caller_supplied_iri_cannot_break_the_query(
    app: FastAPI,
    store: Any,
) -> None:
    """`iri_term` refuses rather than escaping, so injection is a 400.

    `URIRef.n3()` does not escape, so an IRI containing `>` would close the
    term early and whatever followed would be parsed as SPARQL. Refusing is
    the only safe answer, and the caller gets told.
    """
    store({})

    for attempt in (
        "https://x/a> } ; DROP GRAPH <urn:pulse:graph:canonical> #",
        "https://x/a b",
        'https://x/a"b',
    ):
        status_code, body = _get(app, "/v2/graph/entity", iri=attempt)
        assert status_code == HTTP_BAD_REQUEST, attempt
        assert "not usable as a SPARQL term" in json.dumps(body)


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------


def test_status_reports_the_two_graphs_separately(app: FastAPI, store: Any) -> None:
    """Canonical and provenance answer different questions.

    A provenance count of 0 beside a healthy canonical count is the normal
    state today — only *contested* values are recorded — so collapsing them
    into one total would make that indistinguishable from an empty store.
    """
    store(
        {
            "GROUP BY ?g": [
                {"g": _uri(CANONICAL_GRAPH), "n": _lit("3094")},
                {"g": _uri(PROV_GRAPH), "n": _lit("7")},
                {"g": _uri("urn:pulse:output:r1:github"), "n": _lit("29")},
            ],
            "GROUP BY ?type": [
                {"type": _uri("http://schema.org/Person"), "n": _lit("109")},
                {"type": _uri("https://open-pulse.epfl.ch/ontology#Contribution"), "n": _lit("46")},
            ],
            "ExtractionRun": [{"n": _lit("119")}],
        },
    )

    status_code, body = _get(app, "/v2/graph/status")

    assert status_code == HTTP_OK
    assert body["canonical_triples"] == 3094  # noqa: PLR2004
    assert body["provenance_triples"] == 7  # noqa: PLR2004
    assert body["named_graphs"] == 3  # noqa: PLR2004
    assert body["extraction_runs"] == 119  # noqa: PLR2004
    # Types come back compacted, not as full IRIs.
    assert body["canonical_entities_by_type"] == {
        "schema:Person": 109,
        "pulse:Contribution": 46,
    }


def test_an_unconfigured_store_is_503_not_404(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    """"Nothing about that IRI" and "no store" are different answers.

    A caller that cannot tell them apart will cache the wrong one.
    """
    monkeypatch.setenv("API_TOKEN", TOKEN)
    monkeypatch.delenv("V2_SUBSTRATE_STORE_URL", raising=False)

    status_code, body = _get(app, "/v2/graph/status")

    assert status_code == HTTP_UNAVAILABLE
    assert "no graph store configured" in json.dumps(body)


# --------------------------------------------------------------------------
# entity
# --------------------------------------------------------------------------


def test_an_entity_comes_back_as_json_ld(app: FastAPI, store: Any) -> None:
    """The same shape `/v2/extract` returns for the same entity.

    Deliberately: a caller reading an entity out of the store and one reading
    it out of an extraction should not need two parsers. `graph:canonical` is
    plain RDF, so JSON-LD costs nothing here — unlike `graph:prov`.
    """
    store(
        {
            "GRAPH <" + CANONICAL_GRAPH + ">": [
                {
                    "p": _uri("http://www.w3.org/1999/02/22-rdf-syntax-ns#type"),
                    "o": _uri("http://schema.org/Person"),
                },
                {"p": _uri("http://schema.org/name"), "o": _lit("Jane Doe")},
                {
                    "p": _uri("https://open-pulse.epfl.ch/ontology#hasProfile"),
                    "o": _uri(PROFILE),
                },
            ],
        },
    )

    status_code, body = _get(app, "/v2/graph/entity", iri=ORCID_IRI)

    assert status_code == HTTP_OK
    node = body["@graph"][0]
    assert node["@id"] == ORCID_IRI
    assert node["@type"] == "schema:Person"
    assert node["schema:name"] == ["Jane Doe"]
    # A reference stays a reference, compacted.
    assert node["pulse:hasProfile"] == [{"@id": PROFILE}]
    assert body["@context"]


def test_an_unknown_entity_is_404(app: FastAPI, store: Any) -> None:
    store({})

    status_code, _body = _get(app, "/v2/graph/entity", iri="https://example.org/nobody")

    assert status_code == HTTP_NOT_FOUND


def test_an_integer_keeps_its_type(app: FastAPI, store: Any) -> None:
    """A count returned as a string would not compare or sort for a consumer."""
    store(
        {
            "GRAPH <" + CANONICAL_GRAPH + ">": [
                {
                    "p": _uri("https://open-pulse.epfl.ch/ontology#repositoryStars"),
                    "o": _lit("1497", "http://www.w3.org/2001/XMLSchema#integer"),
                },
            ],
        },
    )

    _status, body = _get(app, "/v2/graph/entity", iri="https://github.com/acme/tool")

    assert body["@graph"][0]["pulse:repositoryStars"] == [1497]


# --------------------------------------------------------------------------
# provenance
# --------------------------------------------------------------------------


def test_provenance_resolves_the_run_not_just_the_output(
    app: FastAPI,
    store: Any,
) -> None:
    """`prov:wasDerivedFrom` names the output; the run is one hop further.

    A caller asking "which run said this" should not need two requests, so the
    query joins through `prov:wasGeneratedBy` in the meta graph.
    """
    store(
        {
            "wasDerivedFrom": [
                {
                    "p": _uri("http://schema.org/name"),
                    "o": _lit("Jane Doe"),
                    "output": _uri("urn:pulse:output:run-b:github"),
                    "run": _uri("urn:pulse:run:run-b"),
                    "kind": _lit("most-complete-source"),
                    "count": _lit("2", "http://www.w3.org/2001/XMLSchema#integer"),
                    "first": _lit("2026-09-01T09:00:00"),
                    "last": _lit("2026-09-09T11:00:00"),
                },
            ],
            "samePersonAs": [{"other": _uri("https://github.com/jane")}],
        },
    )

    status_code, body = _get(app, "/v2/graph/provenance", subject=ORCID_IRI)

    assert status_code == HTTP_OK
    record = body["records"][0]
    assert record["property"] == "schema:name"
    assert record["value"] == "Jane Doe"
    assert record["derived_from"] == "urn:pulse:output:run-b:github"
    assert record["run"] == "urn:pulse:run:run-b"
    assert record["observation_kind"] == "most-complete-source"
    assert record["observation_count"] == 2  # noqa: PLR2004
    assert record["first_observed_on"] == "2026-09-01T09:00:00"
    # The identity links are separate: plain triples, not annotations.
    assert body["same_as"] == ["https://github.com/jane"]


def test_no_records_means_nothing_was_contested(app: FastAPI, store: Any) -> None:
    """Not "no provenance" — the distinction matters and is easy to misread.

    A single-source value is already attributed by the named graph it sits in,
    so the writer records nothing for it. Over the real 119-run corpus *every*
    subject answers this way, because it produces zero contested values.
    """
    store({})

    status_code, body = _get(app, "/v2/graph/provenance", subject=ORCID_IRI)

    assert status_code == HTTP_OK
    assert body["records"] == []
    assert body["same_as"] == []


def test_the_property_filter_is_expanded_before_it_is_bound(
    app: FastAPI,
    store: Any,
) -> None:
    """A caller passes `schema:name`; SPARQL needs the full IRI.

    And it goes through `iri_term` after expansion, so a compact form that
    expands to something unusable is still refused.
    """
    fake = store({"wasDerivedFrom": []})

    status_code, _body = _get(
        app,
        "/v2/graph/provenance",
        subject=ORCID_IRI,
        property="schema:name",
    )

    assert status_code == HTTP_OK
    assert any("<http://schema.org/name>" in query for query in fake.queries)


def test_a_bad_property_filter_is_400(app: FastAPI, store: Any) -> None:
    store({})

    status_code, body = _get(
        app,
        "/v2/graph/provenance",
        subject=ORCID_IRI,
        property="http://x/a b",
    )

    assert status_code == HTTP_BAD_REQUEST
    assert "not usable" in json.dumps(body)


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["/v2/graph/status", "/v2/graph/entity", "/v2/graph/provenance"],
)
def test_every_graph_endpoint_requires_a_bearer_token(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    """These expose everything the store has accumulated across every run.

    `/v2/health` is open; this is not.
    """
    monkeypatch.setenv("API_TOKEN", TOKEN)

    status_code, _body = _get(app, path, token=None, iri=ORCID_IRI, subject=ORCID_IRI)

    assert status_code == HTTP_UNAUTHORIZED
