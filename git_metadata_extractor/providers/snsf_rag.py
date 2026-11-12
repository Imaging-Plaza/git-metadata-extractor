"""HTTP retrieval adapter; indexing and storage belong to the sources service."""

from __future__ import annotations

from typing import Any, Literal

from git_metadata_extractor.providers._remote_retrieval import (
    RemoteRetrievalProvider,
    build_remote_provider,
)

ScopeMode = Literal["epfl", "ethz", "eth_domain", "switzerland"]
_DEFAULT_TOP_K = 10


class SnsfRagProvider(RemoteRetrievalProvider):
    provider_name = "snsf"

    async def search(
        self,
        query: str,
        *,
        scope_mode: ScopeMode = "switzerland",
        top_k: int = _DEFAULT_TOP_K,
        filters: dict[str, Any] | None = None,
        rerank: bool = False,
    ) -> list[dict[str, Any]]:
        return await self._call(
            "search",
            {
                "query": query,
                "scope_mode": scope_mode,
                "top_k": top_k,
                "filters": filters,
                "rerank": rerank,
            },
            empty=[],
        )


def build_default_provider(client=None) -> SnsfRagProvider | None:
    return build_remote_provider(SnsfRagProvider, client)
