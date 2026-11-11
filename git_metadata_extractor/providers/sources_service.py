"""Typed read-only client for open-pulse-sources' published HTTP endpoints.

Search and manifest are supported. Record/chunk hydration and source-build
metadata are not guaranteed by the current server contract.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from hashlib import sha256
from http import HTTPStatus
from typing import Annotated, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import (
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    ValidationError,
    field_validator,
)

from git_metadata_extractor.models import DataModel

IndexName = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=100)]
_INDEX = TypeAdapter(IndexName)
MAX_TIMEOUT_SECONDS = 300
_backend: ContextVar[str | None] = ContextVar("sources_backend", default=None)


def current_sources_fingerprint() -> str | None:
    return _backend.get()


@contextmanager
def sources_scope(client: SourcesClient | None):
    token = _backend.set(client.cache_identity if client is not None else None)
    try:
        yield
    finally:
        _backend.reset(token)


class SourceSearchRequest(DataModel):
    query: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=10, ge=1, le=200)
    candidate_k: int | None = Field(default=None, ge=1, le=1000)
    filter_payload: dict[str, JsonValue] | None = None
    target: str | None = None

    @field_validator("query")
    @classmethod
    def nonblank_query(cls, value: str) -> str:
        if not value.strip():
            message = "query must contain non-whitespace characters"
            raise ValueError(message)
        return value


class SourceHit(DataModel):
    # The service deliberately allows index-specific fields on a hit.
    model_config = ConfigDict(extra="allow")
    id: str
    vector_score: float | None = Field(default=None, allow_inf_nan=False)
    rerank_score: float | None = Field(default=None, allow_inf_nan=False)
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    entity: dict[str, JsonValue] | None = None


class SourceSearchResponse(DataModel):
    index_name: IndexName
    query: str
    target: str | None = None
    hits: list[SourceHit]
    extra: dict[str, JsonValue] | None = None


class SourceManifestEntry(DataModel):
    model_config = ConfigDict(extra="allow")
    name: str
    duckdb: str
    entity_types: list[str]
    backend: str
    surface_as_source: bool
    id_shape: str
    structured_query: bool = False


class SourceResult(DataModel):
    status: Literal[
        "ok",
        "no_matches",
        "unavailable",
        "unauthorized",
        "unsupported",
        "invalid_response",
        "error",
    ]
    index_name: str | None = None
    hits: list[SourceHit] = Field(default_factory=list)
    sources: list[SourceManifestEntry] = Field(default_factory=list)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    error: str | None = None


class SourcesClient:
    """Owns one asynchronous HTTP pool; never opens index stores or embeds text."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 30,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            message = "SOURCES_API_URL must be an HTTP(S) service root without credentials, query or fragment"
            raise ValueError(message)
        if not token.strip():
            message = "SOURCES_API_TOKEN is required when SOURCES_API_URL is configured"
            raise ValueError(message)
        if not 0 < timeout <= MAX_TIMEOUT_SECONDS:
            message = "SOURCES_API_TIMEOUT_SECONDS must be between 0 and 300"
            raise ValueError(message)
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            follow_redirects=False,
            transport=transport,
        )
        self.cache_identity = sha256(base_url.rstrip("/").encode()).hexdigest()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(
        self, method: str, path: str, **kwargs,
    ) -> httpx.Response | SourceResult:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.RequestError:
            return SourceResult(
                status="unavailable", error="Sources service request failed",
            )
        if response.is_success:
            return response
        if response.status_code in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}:
            status = "unauthorized"
        elif response.status_code == HTTPStatus.NOT_FOUND:
            status = "unsupported"
        elif response.status_code == HTTPStatus.TOO_MANY_REQUESTS or response.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR:
            status = "unavailable"
        else:
            status = "error"
        # Do not expose upstream response bodies, headers or credential-bearing URLs.
        return SourceResult(
            status=status, error=f"Sources service returned HTTP {response.status_code}",
        )

    async def search(self, index: str, request: SourceSearchRequest) -> SourceResult:
        index = _INDEX.validate_python(index)
        response = await self._request(
            "POST",
            f"v2/indices/{index}/search",
            json=request.model_dump(exclude_none=True),
        )
        if isinstance(response, SourceResult):
            return response.model_copy(update={"index_name": index})
        try:
            result = SourceSearchResponse.model_validate_json(response.content)
            if result.index_name != index or result.query != request.query:
                return SourceResult(
                    status="invalid_response",
                    index_name=index,
                    error="Sources response does not match the request",
                )
        except ValidationError:
            return SourceResult(
                status="invalid_response",
                index_name=index,
                error="Sources response violates the search contract",
            )
        if result.extra and result.extra.get("error") is not None:
            return SourceResult(
                status="error",
                index_name=index,
                error="Sources service reported a search failure",
            )
        return SourceResult(
            status="ok" if result.hits else "no_matches",
            index_name=index,
            hits=result.hits,
            metadata=result.extra or {},
        )

    async def manifest(self) -> SourceResult:
        response = await self._request("GET", "v2/manifest")
        if isinstance(response, SourceResult):
            return response
        try:
            sources = TypeAdapter(list[SourceManifestEntry]).validate_json(
                response.content,
            )
        except ValidationError:
            return SourceResult(
                status="invalid_response",
                error="Sources response violates the manifest contract",
            )
        return SourceResult(status="ok", sources=sources)
