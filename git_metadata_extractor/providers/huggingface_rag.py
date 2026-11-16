"""HTTP retrieval adapter; indexing and storage belong to the sources service."""

from __future__ import annotations

from typing import Any, Literal

from git_metadata_extractor.providers._remote_retrieval import (
    RemoteRetrievalProvider,
    build_remote_provider,
)

CollectionName = Literal["models", "datasets", "spaces", "orgs"]
_DEFAULT_TOP_K = 10


class HuggingFaceRagProvider(RemoteRetrievalProvider):
    provider_name = "huggingface"

    async def lineage(self, repo_id: str, *, depth: int = 3) -> dict[str, Any]:
        return await self._call(
            "lineage",
            {"repo_id": repo_id, "depth": depth},
            empty={
                "root": repo_id,
                "ancestors": {},
                "descendants": {},
                "edges": [],
                "depth": depth,
            },
        )

    async def search(
        self,
        query: str,
        *,
        collection: CollectionName = "models",
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


def build_default_provider(client=None) -> HuggingFaceRagProvider | None:
    return build_remote_provider(HuggingFaceRagProvider, client)


async def lineage(repo_id: str, *, depth: int = 3) -> dict[str, Any]:
    return await HuggingFaceRagProvider().lineage(repo_id, depth=depth)
