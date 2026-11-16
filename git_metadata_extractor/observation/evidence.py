"""Request-local evidence, separate from the closed canonical entity schemas.

Queries prove retrieval, not derivation. Only a checked citation says a value
was copied from a query; equal values without a citation remain candidates.
Provider bodies and prompts are never exported or cached in this trace.
"""
# ruff: noqa: A002
# `property` is the public citation tool argument, matching RDF terminology.

from __future__ import annotations

import inspect
import json
import re
from contextlib import contextmanager
from contextvars import ContextVar
from copy import copy, deepcopy
from dataclasses import asdict, is_dataclass, replace
from datetime import datetime, timezone
from functools import wraps
from hashlib import sha256
from threading import RLock
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from pydantic import BaseModel

CONTEXT = {
    "prov": "http://www.w3.org/ns/prov#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "schema": "http://schema.org/",
    "gme": "https://open-pulse.epfl.ch/extractor/provenance#",
    "pulse": "https://open-pulse.epfl.ch/ontology#",
    "org": "http://www.w3.org/ns/org#",
    "time": "http://www.w3.org/2006/time#",
}
VERSION = 1
_active: ContextVar[tuple[str, str] | None] = ContextVar(
    "gme_evidence_activity", default=None,
)
_query: ContextVar[dict[str, Any] | None] = ContextVar(
    "gme_evidence_query", default=None,
)
_SENSITIVE = re.compile(
    r"token|password|secret|authorization|api.?key|cookie|email", re.IGNORECASE,
)
_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def _digest(value: Any) -> str:
    return sha256(_json(value).encode()).hexdigest()


def _safe_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "[invalid-url]"
    host = parsed.netloc.rsplit("@", 1)[-1]
    query = [
        (k, "[redacted]" if _SENSITIVE.search(k) else _EMAIL.sub("[redacted-email]", v))
        for k, v in parse_qsl(parsed.query)
    ]
    path = quote(_EMAIL.sub("[redacted-email]", unquote(parsed.path)), safe="/:@-._~")
    return urlunsplit((parsed.scheme, host, path, urlencode(query), ""))


def _safe(value: Any, key: str = "") -> Any:
    if _SENSITIVE.search(key):
        return "[redacted]"
    if isinstance(value, dict):
        return {
            str(k): _safe(v, str(k))
            for k, v in value.items()
            if not str(k).startswith("_")
        }
    if isinstance(value, (tuple, list)):
        return [_safe(v) for v in value[:100]]
    if isinstance(value, str):
        value = re.sub(r"https?://[^\s\"<>]+", lambda m: _safe_url(m[0]), value)
        value = re.sub(r"(?i)bearer\s+\S+", "Bearer [redacted]", value)
        value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[redacted-email]", value)
        return value[:2000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return f"<{type(value).__name__}>"


def _plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True, mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    return value


def _values(value: Any):
    value = _plain(value)
    if isinstance(value, dict):
        for key, item in value.items():
            if not _SENSITIVE.search(str(key)) and not str(key).startswith("_"):
                yield from _values(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _values(item)
    elif value is not None:
        yield value


def _source_url(key: str, value: str) -> str | None:
    if value.startswith(("https://", "http://")):
        url = _safe_url(value)
        return url if url.startswith(("https://", "http://")) else None
    if key == "doi" and value.startswith("10."):
        return "https://doi.org/" + value
    if key in {"orcid", "orcid_id"} and re.fullmatch(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]", value):
        return "https://orcid.org/" + value
    if key in {"ror", "ror_id"} and re.fullmatch(r"0[0-9a-z]{8}", value):
        return "https://ror.org/" + value
    return None


def _sources(value: Any) -> set[str]:
    result = set()
    value = _plain(value)
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).startswith("_") or _SENSITIVE.search(str(key)):
                continue
            if key in {
                "id",
                "@id",
                "url",
                "uri",
                "html_url",
                "doi",
                "orcid",
                "ror",
                "identifier",
                "orcid_id",
                "ror_id",
                "infoscience_url",
                "profile_url",
                "record_url",
            } and isinstance(item, str):
                url = _source_url(key, item)
                if url is not None:
                    result.add(url)
            result.update(_sources(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            result.update(_sources(item))
    return result


def _normal(value: Any) -> Any:
    if isinstance(value, dict) and set(value) in ({"@id"}, {"id"}):
        return value.get("@id", value.get("id"))
    return value


def entities(value: Any):
    """Walk only live entity containers, never diagnostics/excluded entities."""
    if isinstance(value, dict):
        if value.get("id") or value.get("@id"):
            yield value
        else:
            for key, item in value.items():
                if not key.startswith("_") and key != "excluded_entities":
                    yield from entities(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from entities(item)


def state_entities(state: Any) -> list[dict[str, Any]]:
    if state.payload.get("@graph") is not None:
        return list(entities(state.payload["@graph"]))
    if state.assembled is not None:
        return list(
            entities([state.assembled.root_entity, *state.assembled.related_entities]),
        )
    if state.reconciled is not None:
        rec = state.reconciled
        return list(entities([rec.entities, rec.memberships, rec.contributions]))
    return list(entities(state.buckets))


class EvidenceTrace:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.activities: dict[str, dict[str, Any]] = {}
        self.queries: dict[str, dict[str, Any]] = {}
        self.claims: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.citations: dict[tuple[str, str, str, str], str] = {}
        self._lock = RLock()

    def identifier(self, kind: str) -> str:
        return f"urn:pulse:run:{self.run_id}:{kind}:{uuid4()}"

    def active_id(self) -> str | None:
        active = _active.get()
        return active[1] if active and active[0] == self.run_id else None

    def scope(self, activity_id: str | None = None) -> str | None:
        current = activity_id or self.active_id()
        while current:
            node = self.activities[current]
            if node["kind"] in {"llm", "rule_based", "refiner", "agent"}:
                return current
            parent = node.get("parent")
            if not parent:
                return current
            current = parent
        return None

    def eligible_queries(self) -> list[dict[str, Any]]:
        scope = self.scope()
        shared = {
            identifier
            for identifier, node in self.activities.items()
            if node["run_id"] == self.run_id
            and node["status"] == "success"
            and node["name"] in {"context_gather", "LLMContextSummaryAgentV2.run"}
        }
        return [
            q
            for q in list(self.queries.values())
            if q["status"] == "success"
            and (q["scope"] == scope or q["scope"] in shared)
        ]

    def available_sources(self) -> list[dict[str, Any]]:
        return [
            {"query_id": q["id"], "service": q["service"], "sources": q["sources"]}
            for q in self.eligible_queries()
        ]

    def valid_activity(self, identifier: str | None) -> bool:
        while identifier:
            node = self.activities.get(identifier, {})
            if node.get("status") in {"failed", "rejected"}:
                return False
            identifier = node.get("parent")
        return True

    def cite(
        self, *, property: str, value: Any, query_id: str, entity_id: str = "",
    ) -> dict[str, Any]:
        current = self.active_id()
        scope = self.scope()
        entity_id = entity_id or self.activities.get(scope, {}).get("entity_id", "")
        query = next((q for q in self.eligible_queries() if q["id"] == query_id), None)
        if (
            not current
            or not entity_id
            or not query
            or _digest(_normal(value)) not in query["values"]
        ):
            return {
                "accepted": False,
                "reason": "No matching value in a successful query available to this agent",
            }
        self.citations[(scope or "", entity_id, property, _digest(_normal(value)))] = (
            query_id
        )
        return {"accepted": True}

    def observe(
        self, payload: Any, *, entity_id: str = "", force: bool = False,
    ) -> None:
        current = self.active_id()
        if current is None:
            return
        node = self.activities[current]
        candidates = list(entities(payload))
        if not candidates and isinstance(payload, dict) and entity_id:
            candidates = [{"id": entity_id, **payload}]
        with self._lock:
            for entity in candidates:
                subject = str(entity.get("@id") or entity.get("id"))
                for predicate, value in entity.items():
                    if predicate.startswith(("_", "@")) or ":" not in predicate:
                        continue
                    for item in value if isinstance(value, list) else [value]:
                        if item is None:
                            continue
                        digest = _digest(_normal(item))
                        key = (subject, predicate, digest)
                        if (
                            key in self.claims
                            and not force
                            and self.valid_activity(self.claims[key]["activity"])
                        ):
                            continue
                        scope = self.scope()
                        cited = self.citations.get(
                            (scope or "", subject, predicate, digest),
                        )
                        matches = [
                            q["id"]
                            for q in self.eligible_queries()
                            if digest in q["values"]
                        ]
                        self.claims[key] = {
                            "activity": current,
                            "kind": "retrieved" if cited else node["kind"],
                            "query": cited,
                            "matches": matches,
                        }

    def start_query(
        self, service: str, arguments: Any, *, status: str = "running",
    ) -> dict[str, Any]:
        query = {
            "id": self.identifier("query"),
            "activity": self.active_id(),
            "scope": self.scope(),
            "service": service,
            "arguments": _safe(arguments),
            "started": _now(),
            "status": status,
            "sources": [],
            "values": [],
            "cache_hit": False,
        }
        with self._lock:
            self.queries[query["id"]] = query
        return query

    def snapshot(self) -> dict[str, Any]:
        """Serializable cache packet; retains origin run IDs, never provider bodies."""
        with self._lock:
            scope = self.scope()
            owned = {scope} if scope else set()
            for identifier, node in self.activities.items():
                if node.get("parent") in owned:
                    owned.add(identifier)
            queries = {q["id"]: deepcopy(q) for q in self.eligible_queries()}
            # Include failed/attempted queries as history too, but not candidates.
            queries.update(
                {i: deepcopy(q) for i, q in self.queries.items() if q["scope"] in owned},
            )
            needed = owned | {q["activity"] for q in queries.values() if q["activity"]}
            for identifier in list(needed):
                parent = self.activities[identifier].get("parent")
                while parent:
                    needed.add(parent)
                    parent = self.activities[parent].get("parent")
            activities = {i: deepcopy(self.activities[i]) for i in needed}
            # The cache write itself witnesses a complete, validated result.
            for node in activities.values():
                if node["status"] == "running":
                    node.update(status="success", ended=_now())
            return {
                "version": VERSION,
                "activities": activities,
                "queries": queries,
                "claims": [
                    [list(k), deepcopy(v)]
                    for k, v in self.claims.items()
                    if v["activity"] in owned
                ],
            }

    def restore(self, packet: Any) -> None:
        if not isinstance(packet, dict) or packet.get("version") != VERSION:
            return
        with self._lock:
            for identifier, node in packet.get("activities", {}).items():
                self.activities.setdefault(identifier, deepcopy(node))
            for identifier, node in packet.get("queries", {}).items():
                self.queries.setdefault(identifier, deepcopy(node))
            for key, value in packet.get("claims", []):
                self.claims[tuple(key)] = deepcopy(value)

    def _activity_nodes(self) -> list[dict[str, Any]]:
        nodes = []
        for identifier, activity_node in self.activities.items():
            if activity_node["status"] == "running":
                continue
            node = {
                "@id": identifier,
                "@type": "prov:Activity",
                "schema:name": activity_node["name"],
                "gme:kind": activity_node["kind"],
                "gme:status": activity_node["status"],
                "prov:startedAtTime": activity_node["started"],
                "prov:wasInformedBy": {
                    "@id": activity_node.get("parent")
                    or f"urn:pulse:run:{activity_node['run_id']}",
                },
            }
            if activity_node.get("ended"):
                node["prov:endedAtTime"] = activity_node["ended"]
            for key in ("model", "provider", "cache_hit", "tokens_prompt", "tokens_completion", "requests", "tool_calls", "mapping_version", "mapping_fingerprint", "source_contract", "target_contract"):
                if activity_node.get(key) is not None:
                    node[f"gme:{key}"] = activity_node[key]
            if activity_node.get("reused"):
                node["prov:used"] = [{"@id": i} for i in activity_node["reused"]]
            agent_id = "urn:pulse:software-agent:" + _digest(
                [
                    activity_node["name"],
                    activity_node.get("model"),
                    activity_node.get("provider"),
                ],
            )
            node["prov:wasAssociatedWith"] = {"@id": agent_id}
            nodes.append(node)
            nodes.append(
                {
                    "@id": agent_id,
                    "@type": "prov:Agent",
                    "schema:name": activity_node["name"],
                },
            )
        return nodes

    def _query_nodes(self) -> list[dict[str, Any]]:
        nodes = []
        for query in self.queries.values():
            node = {
                "@id": query["id"],
                "@type": ["prov:Activity", "gme:Retrieval"],
                "gme:service": query["service"],
                "gme:status": query["status"],
                "gme:arguments": {"@value": query["arguments"], "@type": "@json"},
                "gme:source": [{"@id": s} for s in query["sources"]],
                "prov:startedAtTime": query["started"],
                "gme:cache_hit": query["cache_hit"],
            }
            if query.get("ended"):
                node["prov:endedAtTime"] = query["ended"]
            if query["activity"]:
                node["prov:wasInformedBy"] = {"@id": query["activity"]}
            if query.get("result_digest"):
                node["gme:resultDigest"] = query["result_digest"]
            nodes.append(node)
        return nodes

    def _assertion_nodes(self, final_nodes: Any) -> list[dict[str, Any]]:
        nodes = []
        for entity in entities(final_nodes):
            subject = str(entity.get("@id") or entity.get("id"))
            for predicate, value in entity.items():
                if predicate.startswith(("_", "@")) or ":" not in predicate:
                    continue
                for item in value if isinstance(value, list) else [value]:
                    if item is None:
                        continue
                    key = (subject, predicate, _digest(_normal(item)))
                    claim = self.claims.get(key)
                    if claim and not self.valid_activity(claim["activity"]):
                        claim = None
                    node = {
                        "@id": f"urn:pulse:run:{self.run_id}:assertion:{_digest(key)}",
                        "@type": "rdf:Statement",
                        "rdf:subject": {"@id": subject},
                        "rdf:predicate": {"@id": predicate},
                        "rdf:object": item
                        if not isinstance(item, dict) or "@id" in item
                        else {"@value": item, "@type": "@json"},
                        "gme:evidenceKind": claim["kind"] if claim else "unattributed",
                    }
                    if claim:
                        node["prov:wasGeneratedBy"] = {"@id": claim["activity"]}
                        if claim.get("query"):
                            node["prov:wasDerivedFrom"] = [{"@id": claim["query"]}]
                        elif claim.get("matches"):
                            node["gme:matchingQuery"] = [
                                {"@id": q} for q in claim["matches"]
                            ]
                    nodes.append(node)
        return nodes

    def document(
        self, final_nodes: Any, *, context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        nodes = [
            *self._activity_nodes(),
            *self._query_nodes(),
            *self._assertion_nodes(final_nodes),
        ]
        # An agent can be invoked many times; only one description per IRI.
        return {
            "@context": {**CONTEXT, **(context or {})},
            "@graph": list({n["@id"]: n for n in nodes}.values()),
        }


def get_trace() -> EvidenceTrace | None:
    from git_metadata_extractor.observation.query_log import (  # noqa: PLC0415 - avoids import cycle
        query_log_var,
    )

    log = query_log_var.get()
    return log.evidence if log is not None else None


@contextmanager
def activity(
    name: str,
    *,
    kind: str,
    entity_id: str = "",
    model: str | None = None,
    provider: str | None = None,
):
    trace = get_trace()
    if trace is None:
        yield None
        return
    parent = trace.active_id()
    identifier = trace.identifier("activity")
    node = {
        "name": name,
        "kind": kind,
        "entity_id": entity_id,
        "run_id": trace.run_id,
        "parent": parent,
        "started": _now(),
        "status": "running",
    }
    if model:
        node.update(model=model, provider=provider)
    trace.activities[identifier] = node
    token = _active.set((trace.run_id, identifier))
    try:
        yield node
    except BaseException:
        node["status"] = "failed"
        raise
    else:
        if node["status"] == "running":
            node["status"] = "success"
    finally:
        node["ended"] = _now()
        _active.reset(token)


def mark_cache_hit() -> None:
    query = _query.get()
    if query is not None:
        query["cache_hit"] = True


class _ObservedProvider:
    def __init__(self, provider: Any, name: str) -> None:
        self._provider = provider
        self._name = name

    def __getattr__(self, name: str) -> Any:  # noqa: C901 - sync and async observation share this boundary
        method = getattr(self._provider, name)
        if (
            not callable(method)
            or name.startswith("_")
            or name in {"close", "health", "is_available"}
        ):
            return method

        @wraps(method)
        def call(*args, **kwargs):
            trace = get_trace()
            if trace is None:
                return method(*args, **kwargs)
            try:
                arguments = dict(
                    inspect.signature(method).bind(*args, **kwargs).arguments,
                )
            except (ValueError, TypeError):
                arguments = {"args": args, **kwargs}
            query = trace.start_query(f"{self._name}.{name}", arguments)

            def finish(result):
                payload = _plain(result)
                if isinstance(payload, dict) and (
                    payload.get("error") or payload.get("fetched") is False
                ):
                    query.update(status="failed", ended=_now())
                    return result
                query.update(
                    status="success",
                    ended=_now(),
                    sources=sorted(_sources(result)),
                    values=sorted({_digest(v) for v in _values(result)}),
                    result_digest=_digest(_plain(result)),
                )
                return result

            async def async_call():
                token = _query.set(query)
                try:
                    return finish(await method(*args, **kwargs))
                except BaseException:
                    query.update(status="failed", ended=_now())
                    raise
                finally:
                    _query.reset(token)

            if inspect.iscoroutinefunction(method):
                return async_call()
            token = _query.set(query)
            try:
                return finish(method(*args, **kwargs))
            except BaseException:
                query.update(status="failed", ended=_now())
                raise
            finally:
                _query.reset(token)

        if inspect.iscoroutinefunction(method):
            @wraps(method)
            async def async_wrapper(*args, **kwargs):
                return await call(*args, **kwargs)

            return async_wrapper
        return call


def observe_provider(provider: Any, name: str) -> Any:
    return (
        provider
        if isinstance(provider, _ObservedProvider)
        else _ObservedProvider(provider, name)
    )


def observe_providers(providers: Any) -> Any:
    if get_trace() is None or not isinstance(providers, BaseModel):
        return providers
    return providers.model_copy(
        update={
            name: observe_provider(getattr(providers, name), name)
            for name in type(providers).model_fields
            if getattr(providers, name) is not None
        },
    )


def observe_tool(tool: Any) -> Any:
    """Keep Pydantic AI's schema/flags intact while observing its result boundary."""
    function = getattr(tool, "function", tool)
    name = getattr(tool, "name", None) or getattr(function, "__name__", "tool")
    wrapped = getattr(
        _ObservedProvider(SimpleNamespace(**{name: function}), "tool"), name,
    )
    if hasattr(tool, "function_schema"):
        observed = copy(tool)
        observed.function = wrapped
        observed.function_schema = replace(tool.function_schema, function=wrapped)
        return observed
    return wrapped


def evidence_tools() -> list[Any]:
    def list_source_evidence() -> list[dict[str, Any]]:
        """List successful retrieval query IDs and record URLs available to this agent."""
        trace = get_trace()
        return trace.available_sources() if trace else []

    def cite_source(
        *, property: str, value: str | int | float | bool, query_id: str, entity_id: str = "",  # noqa: PYI041 - JSON int and float hashes differ
    ) -> dict[str, Any]:
        """Cite a retrieved scalar value for an output property (e.g. schema:name).

        Use a query_id from list_source_evidence. Supply the output entity_id
        when creating an entity. Only cite a value you copied, never an inference.
        The server rejects values absent from the query's actual response.
        """
        trace = get_trace()
        return (
            trace.cite(
                property=property, value=value, query_id=query_id, entity_id=entity_id,
            )
            if trace
            else {"accepted": False}
        )

    return [list_source_evidence, cite_source]
