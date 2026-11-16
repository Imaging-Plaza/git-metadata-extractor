"""HTTP retrieval adapter; indexing and storage belong to the sources service."""

from __future__ import annotations

from collections.abc import Sequence  # noqa: TC003 - runtime signature inspection
from typing import Any, Literal

from git_metadata_extractor.providers._remote_retrieval import (
    RemoteRetrievalProvider,
    build_remote_provider,
)

CollectionName = Literal["chunks", "articles", "persons", "organizations"]
_DEFAULT_TOP_K = 10
_DEFAULT_MAX_CHUNKS = 20


class EthzResearchCollectionRagProvider(RemoteRetrievalProvider):
    provider_name = "ethz_research_collection"

    async def search(
        self,
        query: str,
        *,
        collection: CollectionName = "chunks",
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

    async def fetch_chunks(
        self, article_uuid: str, *, max_chunks: int = _DEFAULT_MAX_CHUNKS,
    ) -> list[dict[str, Any]]:
        return await self._call(
            "fetch_chunks",
            {"article_uuid": article_uuid, "max_chunks": max_chunks},
            empty=[],
        )

    async def fetch_records(
        self, collection: CollectionName, ids: Sequence[str],
    ) -> list[dict[str, Any]]:
        if collection == "chunks":
            message = (
                "Use fetch_chunks(article_uuid) for chunk bodies, not fetch_records."
            )
            raise ValueError(message)
        return await self._call(
            "fetch_records", {"collection": collection, "ids": ids}, empty=[],
        )


def build_default_provider(client=None) -> EthzResearchCollectionRagProvider | None:
    return build_remote_provider(EthzResearchCollectionRagProvider, client)
