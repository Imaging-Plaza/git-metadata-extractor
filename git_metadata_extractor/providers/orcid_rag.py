"""HTTP retrieval adapter; indexing and storage belong to the sources service."""

from __future__ import annotations

from typing import Any, Literal

from git_metadata_extractor.providers._remote_retrieval import (
    RemoteRetrievalProvider,
    build_remote_provider,
)

_DEFAULT_TOP_K = 10
EntityType = Literal["persons", "employments", "educations"]


class OrcidRagProvider(RemoteRetrievalProvider):
    provider_name = "orcid"

    async def search(
        self,
        query: str,
        *,
        entity_type: EntityType = "persons",
        top_k: int = _DEFAULT_TOP_K,
        filters: dict[str, Any] | None = None,
        rerank: bool = False,
    ) -> list[dict[str, Any]]:
        return await self._call(
            "search",
            {
                "query": query,
                "entity_type": entity_type,
                "top_k": top_k,
                "filters": filters,
                "rerank": rerank,
            },
            empty=[],
        )


def build_default_provider(client=None) -> OrcidRagProvider | None:
    return build_remote_provider(OrcidRagProvider, client)
