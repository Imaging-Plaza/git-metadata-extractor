"""HTTP retrieval adapter; indexing and storage belong to the sources service."""

from __future__ import annotations

from typing import Any, Literal

from git_metadata_extractor.providers._remote_retrieval import (
    RemoteRetrievalProvider,
    build_remote_provider,
)

CollectionName = Literal[
    "works", "authors", "institutions", "sources", "topics", "concepts",
]
_DEFAULT_TOP_K = 10


class OpenAlexRagProvider(RemoteRetrievalProvider):
    provider_name = "openalex"

    async def search(
        self,
        query: str,
        *,
        collection: CollectionName = "works",
        top_k: int = _DEFAULT_TOP_K,
        filters: dict[str, Any] | None = None,
        rerank: bool = False,
    ) -> list[dict[str, Any]]:
        return await self._call(
            "search",
            {
                "query": query,
                "collection": collection,
                "top_k": top_k,
                "filters": filters,
                "rerank": rerank,
            },
            empty=[],
        )


def build_default_provider(client=None) -> OpenAlexRagProvider | None:
    return build_remote_provider(OpenAlexRagProvider, client)
