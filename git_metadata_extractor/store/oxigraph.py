"""A minimal Oxigraph client: load quads, count graphs, check health.

Oxigraph is the unification engine the architecture picked, and phase 3 needs
exactly one thing from it — the ability to land a run's named graphs. So this
client stays small and does not try to be a SPARQL library: `load_nquads` for
the write path, `named_graphs` and `graph_triple_count` for verifying that the
write happened, `is_available` for the health endpoint.

**Why the whole run goes in one POST to `/store`.** Oxigraph implements the
SPARQL 1.1 Graph Store HTTP Protocol, where `PUT|POST /store?graph=<iri>`
carries *triples* for one named graph. But `POST /store` with **no** `graph`
parameter and a quad body dispatches each quad to the graph its fourth term
names — so a run's whole substrate, however many platform slices it spans,
lands in a single request. Per-graph PUTs would be N requests and would need
the caller to know the graph list, which is the store's business, not the
pipeline's.

POST rather than PUT, because PUT *replaces* the target graph. The substrate is
append-only: graph IRIs embed the run id, so a retried run of the same
repository writes to the same IRIs and nothing else's data is at stake — but
POST is the semantic that stays correct if that ever stops being true.

RDF-star is deliberately absent. `graph:prov` will carry quoted triples in
phase 5, and Oxigraph is RDF-star native, but rdflib (6.3.2 here) cannot
serialise them — so provenance writes will have to build their payload as
SPARQL `INSERT DATA` text rather than going through this loader. See §3.2 of
ONTOLOGY_V3_REQUIREMENTS.md.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

#: The store's own error vocabulary, so callers do not have to know that the
#: transport is HTTP. The pipeline stage distinguishes them: unreachable is
#: an operational condition that degrades the run, a rejected payload is a bug
#: in what we produced.
DEFAULT_TIMEOUT_SECONDS = 30.0

_NQUADS_MEDIA_TYPE = "application/n-quads"
_SPARQL_JSON_MEDIA_TYPE = "application/sparql-results+json"


class StoreUnavailableError(RuntimeError):
    """The store could not be reached at all."""


class StoreWriteError(RuntimeError):
    """The store was reached and refused the payload."""


class OxigraphStore:
    """Load quads into, and read graph names out of, an Oxigraph server.

    `base_url` is the server root (`http://gme-oxigraph:7878`), not one of its
    endpoints — the paths below are fixed by the protocol, so accepting a full
    endpoint URL would only invite `.../query` being configured where
    `.../store` is needed.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        # Injectable so tests exercise the real request construction —
        # method, path, content type, body — against `httpx.MockTransport`
        # instead of a hand-rolled fake with its own idea of the protocol.
        self._transport = transport

    @property
    def base_url(self) -> str:
        return self._base_url

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
            transport=self._transport,
        )

    async def load_nquads(self, nquads: str) -> int:
        """Merge an N-Quads payload into the store. Returns the byte count.

        An empty payload is a no-op rather than an error: a run that produced
        no substrate (a provider failure before any entity was built) should
        not look like a store problem.
        """
        body = nquads.encode("utf-8")
        if not body.strip():
            return 0
        try:
            async with self._client() as client:
                response = await client.post(
                    "/store",
                    content=body,
                    headers={"Content-Type": _NQUADS_MEDIA_TYPE},
                )
        except httpx.HTTPError as exc:
            message = f"oxigraph unreachable at {self._base_url}: {exc}"
            raise StoreUnavailableError(message) from exc
        if response.status_code >= httpx.codes.BAD_REQUEST:
            # The body carries the parse error and the offending line, which is
            # the only useful thing about a rejected load.
            detail = response.text.strip()[:500]
            message = (
                f"oxigraph rejected {len(body)} bytes of n-quads "
                f"({response.status_code}): {detail}"
            )
            raise StoreWriteError(message)
        return len(body)

    async def select(self, query: str) -> list[dict[str, Any]]:
        """Run a SPARQL SELECT and return its bindings as plain dicts."""
        try:
            async with self._client() as client:
                response = await client.post(
                    "/query",
                    data={"query": query},
                    headers={"Accept": _SPARQL_JSON_MEDIA_TYPE},
                )
        except httpx.HTTPError as exc:
            message = f"oxigraph unreachable at {self._base_url}: {exc}"
            raise StoreUnavailableError(message) from exc
        if response.status_code >= httpx.codes.BAD_REQUEST:
            detail = response.text.strip()[:500]
            message = f"oxigraph query failed ({response.status_code}): {detail}"
            raise StoreWriteError(message)
        payload = response.json()
        return list((payload.get("results") or {}).get("bindings") or [])

    async def update(self, update: str) -> None:
        """Run a SPARQL Update (`DROP`, `INSERT DATA`, ...).

        Separate from `load_nquads` because the two are not interchangeable:
        bulk loading goes through the Graph Store protocol, which has no way to
        *remove* anything, and `graph:canonical` has to be replaced rather than
        accumulated. This is also the path phase 5's provenance writer will
        need — rdflib cannot serialise RDF-star, so quoted triples have to
        arrive as `INSERT DATA` text rather than as quads.
        """
        try:
            async with self._client() as client:
                response = await client.post(
                    "/update",
                    data={"update": update},
                )
        except httpx.HTTPError as exc:
            message = f"oxigraph unreachable at {self._base_url}: {exc}"
            raise StoreUnavailableError(message) from exc
        if response.status_code >= httpx.codes.BAD_REQUEST:
            detail = response.text.strip()[:500]
            message = f"oxigraph update failed ({response.status_code}): {detail}"
            raise StoreWriteError(message)

    async def named_graphs(self) -> dict[str, int]:
        """Every named graph in the store, with its triple count."""
        bindings = await self.select(
            "SELECT ?g (COUNT(*) AS ?n) WHERE { GRAPH ?g { ?s ?p ?o } } "
            "GROUP BY ?g ORDER BY ?g",
        )
        return {
            str(row["g"]["value"]): int(row["n"]["value"])
            for row in bindings
            if "g" in row and "n" in row
        }

    async def graph_triple_count(self, graph_iri: str) -> int:
        """Triples in one named graph. 0 when the graph does not exist."""
        bindings = await self.select(
            f"SELECT (COUNT(*) AS ?n) WHERE {{ GRAPH <{graph_iri}> {{ ?s ?p ?o }} }}",
        )
        if not bindings or "n" not in bindings[0]:
            return 0
        return int(bindings[0]["n"]["value"])

    async def is_available(self) -> bool:
        """Whether the store answers a trivial query. Never raises."""
        try:
            await self.select("SELECT ?s WHERE { ?s ?p ?o } LIMIT 1")
        except (StoreUnavailableError, StoreWriteError, ValueError):
            return False
        return True


def store_from_config(
    url: str | None,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    transport: httpx.AsyncBaseTransport | None = None,
) -> OxigraphStore | None:
    """An `OxigraphStore` when a URL is configured, else None.

    Returning None rather than raising is what makes the substrate writer
    optional: no URL configured means the substrate is still produced and
    inspectable, it just is not stored anywhere.
    """
    if not url or not url.strip():
        return None
    return OxigraphStore(url.strip(), timeout=timeout, transport=transport)


def summarise(sizes: Mapping[str, int]) -> str:
    """`graph=n graph=n` — one log-line summary of what was written."""
    return " ".join(f"{iri}={count}" for iri, count in sorted(sizes.items()))


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "OxigraphStore",
    "StoreUnavailableError",
    "StoreWriteError",
    "store_from_config",
    "summarise",
]
