"""Typed read-only client for open-pulse-sources' published HTTP endpoints.

Direct retrieval preserves the existing service contract. Snapshot retrieval
also observes dataset identities and keeps them scoped to one extraction.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from hashlib import sha256
from http import HTTPStatus
from threading import Lock
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
from git_metadata_extractor.providers.base import ProviderError, ProviderNotFoundError

IndexName = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=100)]
_INDEX = TypeAdapter(IndexName)
_OPERATION = TypeAdapter(Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]*$", max_length=100)])
MAX_TIMEOUT_SECONDS = 300
MAX_VERSION_LENGTH = 100
_backend: ContextVar[str | None] = ContextVar("sources_backend", default=None)
_scope: ContextVar[dict | None] = ContextVar("sources_run", default=None)


class SnapshotDescriptor(DataModel):
    source: IndexName
    id: str = Field(min_length=1, max_length=512)
    version: str = Field(min_length=1, max_length=512)


class _SnapshotAvailability(SnapshotDescriptor):
    available: bool
    methods: list[str]


class _SnapshotManifest(DataModel):
    fingerprint: str = Field(min_length=1, max_length=200)
    sources: dict[IndexName, _SnapshotAvailability]


def current_source_mode() -> Literal["direct", "snapshot"]:
    state = _scope.get()
    return state["mode"] if state else "direct"


def record_source_gap(message: str) -> None:
    state = _scope.get()
    if state is not None and message not in state["gaps"]:
        state["gaps"].append(message)


def current_source_gaps() -> list[str]:
    state = _scope.get()
    return list(state["gaps"]) if state else []


def current_source_snapshots() -> list[SnapshotDescriptor]:
    state = _scope.get()
    if not state:
        return []
    with state["metadata_lock"]:
        return list(state["snapshots"].values())


def current_sources_client() -> SourcesClient | None:
    state = _scope.get()
    return state.get("client") if state else None


def current_sources_version() -> str | None:
    state = _scope.get()
    return state.get("version") if state else None


def current_sources_fingerprint() -> str | None:
    return _backend.get()


@contextmanager
def sources_scope(client: SourcesClient | None):
    token = _backend.set(client.cache_identity if client is not None else None)
    scope_token = _scope.set({"client": client, "version": None, "conflict": False,
                            "mode": getattr(client, "source_mode", "direct"),
                            "gaps": [], "snapshots": {}, "snapshot_conflicts": set(), "expected_snapshots": None,
                            "metadata_lock": Lock()})
    try:
        yield
    finally:
        _scope.reset(scope_token)
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


class SourceOperationResult(DataModel):
    status: str
    data: dict[str, JsonValue] = Field(default_factory=dict)
    error: str | None = None
    service_version: str | None = None


class SourceRetrievalResult(DataModel):
    status: str
    data: dict[str, JsonValue] | list[dict[str, JsonValue]] | None = None
    error: str | None = None
    service_version: str | None = None


class _OperationResponse(DataModel):
    data: dict[str, JsonValue]
    service_version: str | None = Field(default=None, max_length=100)
    snapshots: list[SnapshotDescriptor] = Field(default_factory=list)


class _RetrievalResponse(DataModel):
    data: dict[str, JsonValue] | list[dict[str, JsonValue]]
    service_version: str | None = Field(default=None, max_length=100)
    snapshots: list[SnapshotDescriptor] = Field(default_factory=list)


class SourcesClient:
    """Owns HTTP pools, including a sync snapshot seam; never opens index files."""

    def __init__(  # noqa: PLR0913 - explicit transport configuration
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 30,
        transport: httpx.AsyncBaseTransport | None = None,
        sync_transport: httpx.BaseTransport | None = None,
        source_mode: Literal["direct", "snapshot"] = "direct",
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
        self.source_mode = TypeAdapter(Literal["direct", "snapshot"]).validate_python(source_mode)
        self._identity_seed = base_url.rstrip("/") + (":snapshot" if self.source_mode == "snapshot" else "")
        self.cache_identity = sha256(self._identity_seed.encode()).hexdigest()
        self._sync_client = httpx.Client(
            base_url=base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {token}"}, timeout=timeout,
            follow_redirects=False, transport=sync_transport,
        ) if self.source_mode == "snapshot" else None

    async def aclose(self) -> None:
        await self._client.aclose()
        if self._sync_client is not None:
            self._sync_client.close()

    def _observe_snapshots(self, snapshots: list[SnapshotDescriptor]) -> None:
        state = _scope.get()
        if not state or state["client"] is not self:
            return
        # Provider worker threads inherit this same scope dictionary. Comparison,
        # conflict marking and attribution must be one atomic observation.
        with state["metadata_lock"]:
            for snapshot in snapshots:
                source = snapshot.source
                if source in state["snapshot_conflicts"]:
                    continue
                previous = state["snapshots"].get(source)
                expected = state["expected_snapshots"]
                if (previous is not None and previous != snapshot) or (expected is not None and expected.get(source) != snapshot):
                    state["snapshots"].pop(source, None)
                    state["snapshot_conflicts"].add(source)
                    record_source_gap(f"Snapshot identity changed during extraction: {source}")
                else:
                    state["snapshots"][source] = snapshot

    def _require_snapshots(self, snapshots: list[SnapshotDescriptor]) -> None:
        if self.source_mode == "snapshot" and not snapshots:
            message = "Snapshot response omitted dataset identity"
            record_source_gap(message)
            raise ValueError(message)

    async def prepare_snapshots(self) -> bool:
        if self.source_mode != "snapshot":
            return True
        response = await self._request("GET", "v2/snapshots/manifest")
        if isinstance(response, SourceResult):
            return False
        try:
            parsed = _OperationResponse.model_validate_json(response.content)
            manifest = _SnapshotManifest.model_validate(parsed.data)
        except ValidationError:
            record_source_gap("Snapshot manifest is invalid; extraction cache disabled")
            return False
        self.cache_identity = sha256(f"{self._identity_seed}:{manifest.fingerprint}".encode()).hexdigest()
        state = _scope.get()
        if state and state["client"] is self:
            _backend.set(self.cache_identity)
            state["expected_snapshots"] = {
                source: SnapshotDescriptor(source=source, id=item.id, version=item.version)
                for source, item in manifest.sources.items() if item.available
            }
        return True

    def snapshot_lookup(self, provider: str, method: str, payload: dict):
        """Synchronous provider seam; callers run blocking providers in worker threads."""
        provider, method = _INDEX.validate_python(provider), _INDEX.validate_python(method)
        if self._sync_client is None:
            message = "Snapshot lookup requires snapshot mode"
            raise ProviderError(message)
        try:
            response = self._sync_client.post(f"v2/snapshots/{provider}/{method}", json=payload)
            if response.status_code == HTTPStatus.NOT_FOUND:
                message = f"Snapshot record not found: {provider}/{method}"
                raise ProviderNotFoundError(message)
            response.raise_for_status()
            parsed = _RetrievalResponse.model_validate_json(response.content)
            self._require_snapshots(parsed.snapshots)
        except (httpx.HTTPError, ValueError) as exc:
            message = f"Snapshot lookup unavailable: {provider}/{method}"
            record_source_gap(message)
            raise ProviderError(message) from exc
        self._observe_version(parsed.service_version)
        self._observe_snapshots(parsed.snapshots)
        return parsed.data

    def _observe_version(self, version: str | None) -> None:
        state = _scope.get()
        if not version or version == "unknown" or not state or state["client"] is not self:
            return
        with state["metadata_lock"]:
            if state["conflict"]:
                return
            if state["version"] is not None and state["version"] != version:
                state["version"], state["conflict"] = None, True
            else:
                state["version"] = version

    async def operation(self, name: str, payload: dict) -> SourceOperationResult:
        name = _OPERATION.validate_python(name)
        prefix = "v2/snapshots/extractor" if self.source_mode == "snapshot" else "v2/extractor"
        response = await self._request("POST", f"{prefix}/{name}", json=payload)
        if isinstance(response, SourceResult):
            return SourceOperationResult(status=response.status, error=response.error)
        try:
            parsed = _OperationResponse.model_validate_json(response.content)
            self._require_snapshots(parsed.snapshots)
        except ValueError:
            if self.source_mode == "snapshot":
                record_source_gap(f"Snapshot operation returned an invalid response: {name}")
            return SourceOperationResult(status="invalid_response", error="Sources response violates the operation contract")
        self._observe_version(parsed.service_version)
        self._observe_snapshots(parsed.snapshots)
        return SourceOperationResult(status="ok", **parsed.model_dump(exclude={"snapshots"}))

    async def retrieve(self, provider: str, method: str, payload: dict) -> SourceRetrievalResult:
        provider, method = _INDEX.validate_python(provider), _INDEX.validate_python(method)
        prefix = "v2/snapshots" if self.source_mode == "snapshot" else "v2/retrieval"
        response = await self._request("POST", f"{prefix}/{provider}/{method}", json=payload)
        if isinstance(response, SourceResult):
            return SourceRetrievalResult(status=response.status, error=response.error)
        try:
            parsed = _RetrievalResponse.model_validate_json(response.content)
            self._require_snapshots(parsed.snapshots)
        except ValueError:
            if self.source_mode == "snapshot":
                record_source_gap(f"Snapshot retrieval returned an invalid response: {provider}/{method}")
            return SourceRetrievalResult(status="invalid_response", error="Sources response violates the retrieval contract")
        self._observe_version(parsed.service_version)
        self._observe_snapshots(parsed.snapshots)
        return SourceRetrievalResult(status="ok", **parsed.model_dump(exclude={"snapshots"}))

    async def _request(
        self, method: str, path: str, **kwargs,
    ) -> httpx.Response | SourceResult:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.RequestError:
            if self.source_mode == "snapshot":
                record_source_gap(f"Snapshot request unavailable: {path}")
            return SourceResult(
                status="unavailable", error="Sources service request failed",
            )
        if response.is_success:
            version = response.headers.get("X-Open-Pulse-Sources-Version")
            if version and len(version) <= MAX_VERSION_LENGTH and version != "unknown":
                self._observe_version(version)
            return response
        if self.source_mode == "snapshot":
            record_source_gap(f"Snapshot request unsupported or unavailable: {path} (HTTP {response.status_code})")
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
        if self.source_mode == "snapshot":
            record_source_gap(f"Snapshot vector search unsupported: {index}")
            return SourceResult(status="unsupported", index_name=index, error="Snapshot vector search is unsupported")
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
        if self.source_mode == "snapshot":
            record_source_gap("Direct index manifest unavailable in snapshot mode")
            return SourceResult(status="unsupported", error="Use the snapshot manifest")
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
