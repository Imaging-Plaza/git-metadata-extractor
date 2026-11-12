"""HTTP retrieval adapter; indexing and storage belong to the sources service."""

from __future__ import annotations

from typing import Any

from git_metadata_extractor.providers._remote_retrieval import (
    RemoteRetrievalProvider,
    build_remote_provider,
)

_DEFAULT_TOP_K = 10
_DEFAULT_TOP_K_PER_INDEX = 3


class FederatedRagProvider(RemoteRetrievalProvider):
    provider_name = "federated"

    async def search(  # noqa: PLR0913 - established federated interface
        self,
        query: str,
        *,
        indices: list[str] | None = None,
        entity_type: str | None = None,
        top_k: int = _DEFAULT_TOP_K,
        top_k_per_index: int = _DEFAULT_TOP_K_PER_INDEX,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._call(
            "search",
            {
                "query": query,
                "indices": indices,
                "entity_type": entity_type,
                "top_k": top_k,
                "top_k_per_index": top_k_per_index,
                "filters": filters,
            },
            empty={"hits": [], "by_index": {}, "errors": {}},
        )

    async def lookup(
        self, identifier: str, *, indices: list[str] | None = None,
    ) -> dict[str, Any]:
        return await self._call(
            "lookup",
            {"identifier": identifier, "indices": indices},
            empty={
                "identifier": identifier,
                "records": [],
                "by_index": {},
                "errors": {},
            },
        )


def build_default_provider(client=None) -> FederatedRagProvider | None:
    return build_remote_provider(FederatedRagProvider, client)
