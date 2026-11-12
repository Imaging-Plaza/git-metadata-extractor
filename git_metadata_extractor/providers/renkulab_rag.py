"""HTTP retrieval adapter; indexing and storage belong to the sources service."""

from __future__ import annotations

from typing import Any

from git_metadata_extractor.providers._remote_retrieval import (
    RemoteRetrievalProvider,
    build_remote_provider,
)

_DEFAULT_TOP_K = 10


class RenkulabRagProvider(RemoteRetrievalProvider):
    provider_name = "renkulab"

    async def search(
        self,
        query: str,
        *,
        top_k: int = _DEFAULT_TOP_K,
        entity_types: list[str] | None = None,
        filters: dict[str, Any] | None = None,
        rerank: bool = False,
    ) -> list[dict[str, Any]]:
        return await self._call(
            "search",
            {
                "query": query,
                "top_k": top_k,
                "entity_types": entity_types,
                "filters": filters,
                "rerank": rerank,
            },
            empty=[],
        )


def build_default_provider(client=None) -> RenkulabRagProvider | None:
    return build_remote_provider(RenkulabRagProvider, client)
